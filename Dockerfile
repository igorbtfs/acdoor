# Dockerfile

# 1. Usar uma imagem base oficial do Python
FROM python:3.10-slim

# 2. Definir variáveis de ambiente
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. Criar e definir o diretório de trabalho
WORKDIR /app

# 4. Instalar dependências (copiando só o requirements.txt primeiro para otimizar o cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiar o restante do código do projeto
COPY . .

# 6. Rodar collectstatic
#RUN python manage.py collectstatic --noinput

# 7. (Opcional, mas boa prática) Criar um usuário não-root para rodar a aplicação
RUN adduser --disabled-password --gecos "" appuser

# 8. Dê a propriedade da pasta /app para o nosso usuário recém-criado
RUN chown -R appuser:appuser /app

# O Docker criará a pasta do volume como root, então damos permissão a ela também
RUN mkdir -p /app/staticfiles && chown -R appuser:appuser /app/staticfiles

USER appuser

# A porta que o Gunicorn vai expor DENTRO do container
EXPOSE 8000

# O comando para iniciar a aplicação (será chamado pelo docker-compose)
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
