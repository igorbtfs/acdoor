// secrets.example.h
// Copie este arquivo para "secrets.h" (mesma pasta) e preencha os valores.
// O secrets.h esta no .gitignore: nunca envie credenciais para o repositorio.
#pragma once

// 1. REDE WI-FI
const char* ssid = "NOME_DA_REDE";
const char* password = "SENHA_DA_REDE";

// 2. ENDERECO DO SERVIDOR DJANGO
// Use o IP (e a porta) do servidor na rede local.
// Ex.: "http://192.168.1.15/api/v1/log-access/"
const char* logServerName = "http://IP_DO_SERVIDOR/api/v1/log-access/";

// 3. TOKEN DA API
// Gere no servidor com: python manage.py drf_create_token esp32_device
const char* apiToken = "TOKEN_GERADO_NO_SERVIDOR";
