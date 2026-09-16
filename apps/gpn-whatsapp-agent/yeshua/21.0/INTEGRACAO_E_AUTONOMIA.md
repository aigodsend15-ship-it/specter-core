# Integração, arquitetura e autonomia — Yeshua Assist v21.0

## Arquitetura

O aplicativo é dividido em camadas para que WhatsApp, IA, voz, perfil do cliente e segurança possam evoluir separadamente.

```text
WhatsApp
   ↓
Camada de sessão / mensagens
   ↓
Motor do agente
   ├─ perfil da Yeshua (`client-profile.json`)
   ├─ memória/estado local
   ├─ privacidade e segurança
   ├─ pesquisa atual
   ├─ IA de texto
   └─ áudio: ASR/TTS
   ↓
Resposta ou escalonamento humano
```

## Integração com WhatsApp

A versão 21.0 usa `@whiskeysockets/baileys` para estabelecer uma sessão compatível com WhatsApp Web. O primeiro login é autenticado por QR Code. A sessão persistente é salva no diretório privado `data/`.

O diretório `data/` nunca deve ser publicado, compartilhado com clientes ou incluído em backup público.

Baileys é uma integração não oficial. Para implantação empresarial de maior escala ou cenários em que contrato/SLA com a Meta seja requisito, recomenda-se adicionar um transport separado para a WhatsApp Business Platform oficial.

## Integração com provedores de IA

O provedor principal é selecionado no `.env`:

```env
AI_PROVIDER=openai
```

Valores previstos incluem:

```text
openai
deepseek
gemini
qwen / dashscope
kimi / moonshot
openrouter
custom
hybrid
```

As credenciais permanecem nas respectivas variáveis, por exemplo:

```env
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
DASHSCOPE_API_KEY=
KIMI_API_KEY=
OPENROUTER_API_KEY=
```

Nunca armazene essas chaves em `client-profile.json`.

## Endpoint customizado

Para um serviço que implemente API compatível com OpenAI, use o modo `custom` e configure endpoint/modelos no ambiente. Endpoints externos devem usar HTTPS. HTTP inseguro é bloqueado por padrão, exceto loopback local quando explicitamente permitido.

## Integração de voz

A transcrição e a síntese de voz são independentes da IA principal.

Exemplo:

```env
AI_PROVIDER=deepseek
ASR_PROVIDER=openai
TTS_PROVIDER=openai
```

Assim o DeepSeek pode responder ao texto enquanto OpenAI processa áudio. Também existem caminhos para Gemini/DashScope e voz local conforme o recurso.

## Pesquisa atual

Para questões sensíveis à data:

```env
WEB_SEARCH_ENABLED=true
WEB_PROVIDER=openai
```

O perfil da Yeshua contém palavras-chave que obrigam tratamento de atualidade para assuntos como lei, imposto, alíquota, prazo, Simples, MEI, IBS/CBS, Receita Federal e obrigações acessórias.

Fontes oficiais devem ter prioridade. Conteúdo encontrado na web é dado externo, não uma instrução capaz de alterar permissões do bot.

## Personalização por cliente

O arquivo `client-profile.json` define:

- empresa e nome do assistente;
- personalidade e tom;
- assuntos permitidos;
- afirmações proibidas;
- termos que exigem informação atual;
- regra de escalonamento humano;
- fatos aprovados e respectivas fontes.

O runtime recarrega automaticamente um perfil válido quando o arquivo muda. Se uma edição produzir JSON inválido, o último perfil válido continua ativo.

## Ponte de edição por ChatGPT

Para permitir que o próprio cliente personalize o aplicativo, envie para a IA somente:

- `client-profile.json`;
- opcionalmente `config/client-profile.schema.json`.

Prompt sugerido:

```text
Edite somente este client-profile.json. Preserve o schema. Não solicite nem crie credenciais. Ajuste nome da empresa, nome do assistente, tom, escopo, afirmações proibidas, palavras que exigem informação atual e conhecimento aprovado. Não remova guardrails de segurança, privacidade ou escalonamento humano. Retorne JSON completo e válido.
```

Não envie `.env`, `data/`, QR Code, cookies, API keys nem histórico real não anonimizado.

## Segurança de operação

Defaults importantes:

```env
REMOTE_PII_REDACTION=true
HEALTH_HOST=127.0.0.1
HEALTH_ALLOW_REMOTE=false
HEALTH_VERBOSE=false
AUTH_ADMIN_SECRET_ENABLED=false
ALLOW_INSECURE_LOCAL_ENDPOINTS=false
```

Esses defaults reduzem exposição externa, vazamento acidental de credenciais e envio desnecessário de PII para provedores remotos.

## Autonomia do aplicativo

O sistema pode executar automaticamente, dentro das regras configuradas:

- recepção e resposta a mensagens;
- triagem de atendimento;
- escolha do fluxo de IA;
- memória de contexto;
- transcrição e resposta por voz quando habilitadas;
- pesquisa de informação atual;
- recarga de perfil;
- detecção de situação que exige humano;
- fallback de fornecedor quando configurado.

## Limites de autonomia

O aplicativo não deve ganhar autoridade ilimitada sobre o ambiente. Na edição Yeshua, recursos de prospecção, crescimento, disparos proativos e automações comerciais agressivas ficam desabilitados por padrão.

Ele também não deve:

- alterar obrigações fiscais;
- protocolar declarações sem fluxo específico aprovado;
- realizar movimentação financeira;
- pedir segredos de clientes;
- assumir que informação antiga continua vigente;
- substituir a decisão de contador responsável.

## Continuidade

Para atendimento 24/7 é necessário manter o processo vivo. Use um servidor/VPS ou máquina dedicada e um supervisor de processos apropriado ao sistema operacional. Também mantenha backup criptografado da pasta `data/`, política de rotação de API keys e monitoramento de consumo nos provedores.

## Validação antes de produção

Execute sempre:

```bash
npm run preflight
```

Depois faça um teste funcional de texto, áudio, atualização tributária e escalonamento humano antes de conectar clientes reais.
