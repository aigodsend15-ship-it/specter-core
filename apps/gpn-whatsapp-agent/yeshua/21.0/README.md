# Yeshua Contabilidade — Assistente WhatsApp IA v21.0

Pacote personalizado do GPN WhatsApp Agent para atendimento inicial da Yeshua Contabilidade.

## O que ele faz

- atende clientes no WhatsApp e faz triagem inicial;
- responde dúvidas frequentes sobre os serviços da Yeshua;
- pode usar OpenAI, DeepSeek, Gemini, Qwen/DashScope, Kimi/Moonshot, OpenRouter ou endpoint OpenAI-compatible;
- separa IA de texto, transcrição de áudio e voz sintetizada;
- pesquisa informação atual quando o assunto depende de legislação, tributos ou prazos;
- mantém memória operacional local;
- recarrega a personalidade e regras do cliente pelo `client-profile.json` sem recompilar o aplicativo;
- encaminha para atendimento humano quando a resposta exige análise profissional do caso concreto.

## Instalação

Leia [INSTALACAO_FACIL.md](./INSTALACAO_FACIL.md). O aplicativo requer Node.js 20+ e FFmpeg para recursos de áudio.

## Integração e personalização

Leia [INTEGRACAO_E_AUTONOMIA.md](./INTEGRACAO_E_AUTONOMIA.md). API keys ficam somente no arquivo local `.env`; identidade, personalidade e conhecimento aprovado ficam no `client-profile.json`.

## Guia executivo para Anderson Régis

Leia [GUIA_ANDERSON_REGIS.md](./GUIA_ANDERSON_REGIS.md). O documento explica, sem exigir conhecimento de programação, o que o aplicativo faz, como é instalado, como se integra às IAs e quais são os limites da autonomia.

## Download do pacote

Versão validada: `GPN-21.0-YESHUA-CONTABILIDADE.zip`

SHA-256 esperado: `3b8602ce3e07fe5c940509651549789a1c01b918749461e047e6b613a116699d`

> Importante: não publique `.env`, API keys, QR do WhatsApp nem a pasta `data/`. O pacote distribuído contém apenas `.env.example` sem credenciais.

## Segurança

- health local em `127.0.0.1` por padrão;
- diagnósticos detalhados protegidos por token quando ativados;
- HTTPS obrigatório para endpoints externos por padrão;
- mascaramento opcional de CPF/CNPJ/e-mail/telefone antes de chamadas remotas;
- segredo administrativo remoto desativado por padrão;
- limites de mídia antes do FFmpeg;
- perguntas tributárias sensíveis à data devem usar fonte oficial atual e escalar para contador humano quando necessário.

## WhatsApp

A versão atual usa Baileys para a sessão do WhatsApp Web. É uma integração não oficial. Para implantação empresarial de maior escala, o caminho recomendado é também oferecer um transporte compatível com a WhatsApp Business Platform oficial.
