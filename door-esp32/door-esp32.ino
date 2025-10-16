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

// 2. ENDEREÇO DO SEU SERVIDOR DJANGO
// Use o IP da sua máquina na rede local. Ex: "http://192.168.1.15:8000/api/v1/log-access/"
const char* logServerName = "http://192.168.1.2:8000/api/v1/log-access/";
const char* serverName = "http://192.168.1.2:8000/api/v1/bolsistas/";

// Token de API gerado com 'manage.py drf_create_token esp32_device'
const char* apiToken = "160ed41d1026044dd7a06e605e15d76c0ccb945e";

// Pinos do Leitor RFID RC522
// --- CONFIGURAÇÃO DOS PINOS (Hardware) ---

// Pinos para o leitor RFID MFRC522 (conexão SPI padrão)
#define RST_PIN   22  // Pino de Reset
#define SS_PIN    5   // Pino "Slave Select" ou "Chip Select"

// Pino que aciona o módulo relé
#define RELAY_PIN 21

// --- FIM DAS CONFIGURAÇÕES ---


// Instancia o objeto do leitor RFID
MFRC522 mfrc522(SS_PIN, RST_PIN);


/**
 * @brief Função de inicialização principal. Executada uma vez quando o ESP32 liga.
 */
void setup() {
    Serial.begin(115200);
    Serial.println("\n\nAC-Door IFRS v2.0 - Iniciando...");

    // Configura o pino do relé como saída e garante que a porta comece travada
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW); // LOW = Travado (pode ser HIGH dependendo do seu relé)

    // Inicializa os barramentos de comunicação
    SPI.begin();
    mfrc522.PCD_Init(); // Inicia o leitor MFRC522

    Serial.println("Leitor RFID inicializado com sucesso.");
    
    // Conecta-se à rede Wi-Fi
    setupWifi();

    Serial.println("\n>>> Sistema pronto! Aproxime um cartao. <<<");
}

/**
 * @brief Loop principal. Executado repetidamente após o setup.
 */
void loop() {
    // Procura por um novo cartão. Se encontrado, lê o UID.
    if (mfrc522.PICC_IsNewCardPresent() && mfrc522.PICC_ReadCardSerial()) {
        handleRfidScan();
        
        // Coloca o cartão em modo "halt" para permitir a leitura de um novo cartão.
        mfrc522.PICC_HaltA();
    }
}

/**
 * @brief Lida com a leitura de um cartão, envia para o servidor e age na resposta.
 */
void handleRfidScan() {
    String scannedUid = getCardUid();
    Serial.println("=============================================");
    Serial.print("Cartao lido com UID: ");
    Serial.println(scannedUid);
    Serial.println("Verificando permissao com o servidor...");

    // Pergunta ao servidor se o acesso é permitido para este UID
    bool accessGranted = checkAccessWithServer(scannedUid);

    // Age de acordo com a resposta do servidor
    if (accessGranted) {
        Serial.println("=> RESPOSTA DO SERVIDOR: ACESSO PERMITIDO! Abrindo a porta...");
        openDoor();
    } else {
        Serial.println("=> RESPOSTA DO SERVIDOR: ACESSO NEGADO!");
    }
    
    delay(2000); // Um delay para evitar leituras duplas e dar tempo de ler o log
    Serial.println("\n>>> Sistema pronto! Aproxime um cartao. <<<");
}

/**
 * @brief Envia o UID para o servidor Django e retorna true se o acesso for permitido.
 * @param uid O UID do cartão lido, no formato String.
 * @return true se o servidor responder com HTTP 200, false caso contrário.
 */
bool checkAccessWithServer(String uid) {
    // Garante que o Wi-Fi está conectado antes de tentar
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("ERRO: Sem conexao WiFi. Nao foi possivel verificar.");
        return false;
    }

    HTTPClient http;
    http.begin(logServerName);
    
    // Define os cabeçalhos da requisição HTTP
    http.addHeader("Authorization", "Token " + String(apiToken));
    http.addHeader("Content-Type", "application/json");

    // Cria o corpo da requisição no formato JSON: {"uid":"SEU_UID_AQUI"}
    String jsonPayload = "{\"uid\":\"" + uid + "\"}";

    // Envia a requisição POST com o payload JSON
    int httpCode = http.POST(jsonPayload);
    
    // Libera os recursos
    http.end();

    // Acesso é concedido apenas se o servidor responder com "200 OK"
    return (httpCode == 200);
}

/**
 * @brief Conecta-se à rede Wi-Fi definida nas configurações.
 */
void setupWifi() {
    Serial.print("Conectando ao WiFi: ");
    Serial.print(ssid);
    WiFi.begin(ssid, password);
    
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    
    Serial.println("\nWiFi conectado com sucesso!");
    Serial.print("Endereco de IP do ESP32: ");
    Serial.println(WiFi.localIP());
}

/**
 * @brief Lê o UID do cartão e o formata como uma String hexadecimal.
 * @return O UID formatado, ex: "A1 B2 C3 D4".
 */
String getCardUid() {
    String content = "";
    for (byte i = 0; i < mfrc522.uid.size; i++) {
        // Adiciona um espaço entre os bytes
        if (i > 0) {
            content += " ";
        }
        // Adiciona um zero à esquerda se o byte for menor que 0x10
        if (mfrc522.uid.uidByte[i] < 0x10) {
            content += "0";
        }
        content += String(mfrc522.uid.uidByte[i], HEX);
    }
    content.toUpperCase(); // Converte para maiúsculas
    return content;
}

/**
 * @brief Aciona o relé para abrir a porta por um tempo determinado.
 */
void openDoor() {
    digitalWrite(RELAY_PIN, HIGH); // HIGH = Destravado (pode ser LOW dependendo do relé)
    delay(5000);                   // Mantém a porta aberta por 5 segundos
    digitalWrite(RELAY_PIN, LOW);  // LOW = Travado
    Serial.println("Porta travada novamente.");
}
