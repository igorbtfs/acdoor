#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <SPI.h>
#include <MFRC522.h>
//#include <Wire.h>
//#include <LiquidCrystal_I2C.h>
#include <vector>

// --- CONFIGURAÇÕES - MODIFIQUE AQUI ---
const char* ssid = "SciFi";
const char* password = "darksouls";

// Endereço do seu servidor Django (use o IP da sua máquina na rede local para testes)
const char* serverName = "http://192.168.1.2:8000/api/v1/bolsistas/";

// Token de API gerado com 'manage.py drf_create_token esp32_device'
const char* apiToken = "160ed41d1026044dd7a06e605e15d76c0ccb945e";

// Pinos do Leitor RFID RC522
#define RST_PIN   22
#define SS_PIN    5
#define RELAY_PIN 21
// --- FIM DAS CONFIGURAÇÕES ---

MFRC522 mfrc522(SS_PIN, RST_PIN);
std::vector<String> validTokens;
unsigned long lastApiUpdateTime = 0;
const long apiUpdateInterval = 300000;

void setup() {
    Serial.begin(115200);
    Serial.println("\n\nAC-Door IFRS - Iniciando...");
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);
    SPI.begin();
    mfrc522.PCD_Init();
    Serial.println("Leitor RFID inicializado.");
    setupWifi();
    fetchValidTokens();
    Serial.println("\nSistema pronto! Aproxime um cartao.");
}

void loop() {
    if (millis() - lastApiUpdateTime > apiUpdateInterval) {
        fetchValidTokens();
    }
    if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
        handleRfidScan();
        mfrc522.PICC_HaltA();
    }
}

void handleRfidScan() {
    String scannedUid = getCardUid();
    Serial.println("=============================================");
    Serial.println("CARTAO DETECTADO!");
    Serial.print("UID: ");
    Serial.println(scannedUid);
    Serial.println("---------------------------------------------");
    Serial.println("DADOS COMPLETOS DO CARTAO (HEX + TEXTO):");

    // --- MUDANÇA PRINCIPAL ---
    // Chamamos nossa nova função customizada em vez da padrão da biblioteca
    dumpCardToSerialWithText();
    // --- FIM DA MUDANÇA ---

    Serial.println("---------------------------------------------");
    Serial.println("Verificando permissao de acesso...");

    if (scannedUid.equalsIgnoreCase("04 5F 6C 8A")) { 
        Serial.println("AVISO: Cartao de cadastro. Faca login na plataforma.");
        delay(2000);
        Serial.println("\nAproxime um cartao.");
        return;
    }

    bool accessGranted = false;
    for (const String& token : validTokens) {
        if (scannedUid.equalsIgnoreCase(token)) {
            accessGranted = true;
            break;
        }
    }

    if (accessGranted) {
        Serial.println("=> RESULTADO: ACESSO PERMITIDO! Abrindo a porta...");
        openDoor();
    } else {
        Serial.println("=> RESULTADO: ACESSO NEGADO!");
    }
    
    delay(2000);
    Serial.println("\nSistema pronto! Aproxime um cartao.");
    Serial.println("=============================================\n");
}

// --- NOVA FUNÇÃO PARA LER E TRADUZIR OS BLOCOS ---
void dumpCardToSerialWithText() {
    MFRC522::PICC_Type piccType = mfrc522.PICC_GetType(mfrc522.uid.sak);
    Serial.print(F("Tipo do Cartao: "));
    Serial.println(mfrc522.PICC_GetTypeName(piccType));

    // Prepara a chave de autenticação (chave de fábrica padrão para MIFARE)
    MFRC522::MIFARE_Key key;
    for (byte i = 0; i < 6; i++) key.keyByte[i] = 0xFF;

    byte buffer[18];
    byte size = sizeof(buffer);

    // Itera por todos os setores do cartão (MIFARE 1K tem 16 setores)
    for (byte sector = 0; sector < 16; sector++) {
        Serial.print(F("Setor ")); 
        if (sector < 10) Serial.print(F(" "));
        Serial.print(sector);

        // Tenta autenticar o setor
        byte firstBlock = sector * 4;
        MFRC522::StatusCode status = mfrc522.PCD_Authenticate(MFRC522::PICC_CMD_MF_AUTH_KEY_A, firstBlock, &key, &(mfrc522.uid));

        if (status != MFRC522::STATUS_OK) {
            Serial.print(F(" -> Falha na autenticacao: "));
            Serial.println(mfrc522.GetStatusCodeName(status));
            continue; // Pula para o próximo setor
        }
        Serial.println(F(" -> Autenticado com sucesso"));

        // Lê os 4 blocos do setor
        for (byte blockOffset = 0; blockOffset < 4; blockOffset++) {
            byte blockAddr = firstBlock + blockOffset;
            Serial.print(F("  Bloco "));
            if (blockAddr < 10) Serial.print(F(" "));
            Serial.print(blockAddr);
            Serial.print(F("  : "));

            // Tenta ler o bloco
            status = mfrc522.MIFARE_Read(blockAddr, buffer, &size);
            if (status != MFRC522::STATUS_OK) {
                Serial.print(F("Falha na leitura: "));
                Serial.println(mfrc522.GetStatusCodeName(status));
                continue; // Pula para o próximo bloco
            }

            // 1. Imprime os dados em formato Hexadecimal
            for (byte i = 0; i < 16; i++) {
                Serial.print(buffer[i] < 0x10 ? " 0" : " ");
                Serial.print(buffer[i], HEX);
            }
            Serial.print(F("  |  "));

            // 2. Imprime os dados em formato de Texto (ASCII)
            for (byte i = 0; i < 16; i++) {
                // Imprime o caractere apenas se ele for "imprimível"
                if (buffer[i] >= 32 && buffer[i] <= 126) {
                    Serial.write(buffer[i]);
                } else {
                    Serial.print(F(".")); // Usa um ponto para caracteres não imprimíveis
                }
            }
            Serial.println();
        }
    }
    // Para a criptografia para liberar o cartão
    mfrc522.PCD_StopCrypto1();
}


// --- Funções Auxiliares (sem alterações) ---

void setupWifi() {
    Serial.print("Conectando ao WiFi: ");
    Serial.print(ssid);
    WiFi.begin(ssid, password);
    
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    
    Serial.println("\nWiFi conectado!");
    Serial.print("Endereco de IP: ");
    Serial.println(WiFi.localIP());
}

void fetchValidTokens() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("Sem conexao WiFi. Nao foi possivel atualizar tokens.");
        return;
    }

    HTTPClient http;
    http.begin(serverName);
    http.addHeader("Authorization", "Token " + String(apiToken));

    Serial.println("Atualizando lista de tokens da API...");

    int httpCode = http.GET();

    if (httpCode == 200) {
        String payload = http.getString();
        DynamicJsonDocument doc(4096);
        DeserializationError error = deserializeJson(doc, payload);

        if (error) {
            Serial.print("Falha no parse do JSON: ");
            Serial.println(error.c_str());
            return;
        }

        validTokens.clear();
        JsonArray array = doc.as<JsonArray>();
        for (JsonObject obj : array) {
            const char* token = obj["rfid_token"];
            if (token) {
                validTokens.push_back(String(token));
            }
        }
        Serial.printf("%d tokens validos carregados.\n", validTokens.size());

    } else {
        Serial.printf("Erro ao contatar a API. Codigo: %d\n", httpCode);
    }

    http.end();
    lastApiUpdateTime = millis();
}

String getCardUid() {
    String content = "";
    for (byte i = 0; i < mfrc522.uid.size; i++) {
        content += String(mfrc522.uid.uidByte[i] < 0x10 ? " 0" : " ");
        content += String(mfrc522.uid.uidByte[i], HEX);
    }
    content.toUpperCase();
    return content.substring(1);
}

void openDoor() {
    digitalWrite(RELAY_PIN, HIGH);
    delay(5000);
    digitalWrite(RELAY_PIN, LOW);
    Serial.println("Porta travada.");
}
