# Instalação fácil — Yeshua Contabilidade v21.0

Este guia foi escrito para instalação sem conhecimento avançado de programação.

## 1. Requisitos

Você precisa de:

- Windows 10/11, Linux ou VPS;
- Node.js 20 ou superior;
- FFmpeg para áudio/voz;
- internet;
- um número de WhatsApp que será conectado por QR Code;
- pelo menos uma API key de IA para os recursos remotos desejados.

## 2. Baixe e extraia o pacote

Baixe `GPN-21.0-YESHUA-CONTABILIDADE.zip`, confirme o SHA-256 indicado no README e extraia a pasta.

Abra o Terminal/PowerShell dentro da pasta extraída.

## 3. Crie a configuração privada

### Windows PowerShell

```powershell
Copy-Item .env.example .env
notepad .env
```

### Linux/macOS

```bash
cp .env.example .env
nano .env
```

Nunca envie o `.env` para GitHub, WhatsApp ou ferramentas públicas de IA.

## 4. Escolha a IA de texto

Exemplo OpenAI:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=SUA_CHAVE_AQUI
```

Exemplo DeepSeek:

```env
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=SUA_CHAVE_AQUI
```

Exemplo Kimi/Moonshot:

```env
AI_PROVIDER=kimi
KIMI_API_KEY=SUA_CHAVE_AQUI
```

Exemplo Qwen/DashScope:

```env
AI_PROVIDER=qwen
DASHSCOPE_API_KEY=SUA_CHAVE_AQUI
```

Você também pode usar `gemini`, `openrouter`, `custom` ou `hybrid` conforme a configuração do provedor.

## 5. Configure voz, se quiser

Texto e voz são independentes. Exemplo usando OpenAI apenas para áudio:

```env
ASR_PROVIDER=openai
TTS_PROVIDER=openai
OPENAI_API_KEY=SUA_CHAVE_AQUI
```

Para manter resposta em texto sem voz sintetizada, deixe o TTS desativado ou configure apenas os recursos necessários.

## 6. Instale as dependências

```bash
npm install
```

Depois rode a validação completa:

```bash
npm run preflight
```

O preflight verifica sintaxe, perfil do cliente e testes automáticos antes da inicialização.

## 7. Inicie

```bash
npm start
```

Na primeira conexão, faça a autenticação do WhatsApp pelo QR Code mostrado no terminal.

## 8. Teste antes de colocar clientes reais

Envie mensagens de teste para confirmar:

1. saudação e identidade Yeshua;
2. pergunta sobre um serviço contábil;
3. áudio curto;
4. pergunta que exija atualização tributária;
5. situação que deva ser encaminhada para um contador humano.

## 9. Personalização sem mexer no código

Edite somente `client-profile.json` para mudar:

- nome do assistente;
- nome da empresa;
- tom de voz;
- temas permitidos;
- frases proibidas;
- regras de escalonamento;
- conhecimento aprovado.

Não coloque chaves de API nesse arquivo.

## 10. Funcionamento 24/7

O aplicativo só trabalha enquanto o processo estiver rodando e houver internet. Para operação contínua, instale-o em um computador que permaneça ligado ou em um servidor/VPS e use um supervisor de processos adequado ao ambiente.

## 11. Backup

A pasta `data/` contém sessão do WhatsApp e estado operacional. Faça backup privado e criptografado. Nunca publique essa pasta.

## Checklist final

- [ ] Node.js 20+ instalado
- [ ] FFmpeg instalado para áudio
- [ ] `.env` criado localmente
- [ ] API key configurada
- [ ] `npm install` concluído
- [ ] `npm run preflight` aprovado
- [ ] QR Code autenticado
- [ ] teste de texto aprovado
- [ ] teste de áudio aprovado, se usado
- [ ] escalonamento para contador humano validado
- [ ] `.env` e `data/` fora do GitHub
