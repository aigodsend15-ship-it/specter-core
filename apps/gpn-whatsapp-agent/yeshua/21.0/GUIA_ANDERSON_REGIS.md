# Guia executivo para Anderson Régis

## O que é este aplicativo

O Yeshua Assist é um atendente digital para WhatsApp criado para reduzir o volume de dúvidas repetitivas, organizar a triagem dos clientes e dar respostas iniciais de forma rápida, mantendo o contador humano como autoridade final nos casos que exigem análise profissional.

Ele não é um simples menu automático. O sistema combina uma sessão de WhatsApp, um motor de inteligência artificial, memória operacional, pesquisa de informação atual e regras específicas da Yeshua Contabilidade.

## Em linguagem simples

Pense nele como uma recepção digital que pode trabalhar continuamente enquanto o computador ou servidor estiver ligado e conectado à internet.

Quando um cliente pergunta algo, o aplicativo identifica o assunto, usa a IA configurada, aplica as regras da Yeshua, consulta informação atual quando necessário e decide entre responder, pedir mais contexto ou encaminhar a questão para um contador.

## O que ele consegue fazer sozinho

- receber e responder mensagens de WhatsApp;
- explicar serviços da Yeshua;
- fazer perguntas de triagem antes do atendimento humano;
- entender áudio quando a transcrição está configurada;
- responder por texto e, opcionalmente, por voz;
- usar provedores diferentes de IA conforme a configuração;
- consultar informação atual quando o tema depende de lei, tributo, prazo ou regra vigente;
- manter contexto da conversa;
- aplicar a personalidade e as regras definidas pela empresa;
- identificar assuntos que exigem validação de um contador;
- continuar funcionando com alterações de personalidade sem precisar recompilar o programa.

## O que ele não deve fazer sozinho

O bot não deve emitir parecer definitivo sobre um caso tributário individual, prometer economia fiscal, alterar declarações, movimentar dinheiro, pedir senha bancária ou gov.br, inventar alíquota/prazo ou substituir o profissional responsável.

Quando faltarem documentos, quando a situação depender de interpretação profissional ou quando uma regra não puder ser confirmada em fonte oficial atual, o comportamento correto é informar que o caso precisa de validação humana.

## Como a inteligência artificial entra no sistema

A inteligência de texto pode ser trocada sem reescrever o aplicativo. Hoje a arquitetura aceita OpenAI, DeepSeek, Gemini, Qwen/DashScope, Kimi/Moonshot, OpenRouter e serviços compatíveis com o padrão OpenAI.

Isso significa que a Yeshua não fica presa a uma única empresa de IA. A chave e o provedor são definidos no arquivo privado `.env`.

Exemplo conceitual:

```text
WhatsApp do cliente
        ↓
Yeshua Assist
        ↓
Regras da Yeshua + contexto + segurança
        ↓
Provedor de IA escolhido
        ↓
Resposta validada pelas regras
        ↓
Cliente ou contador humano
```

## Voz também é independente

O provedor de voz não precisa ser o mesmo provedor do chat. Por exemplo, o texto pode usar DeepSeek e a transcrição/voz pode usar OpenAI. Essa separação permite trocar preço, qualidade e fornecedor sem alterar o restante do sistema.

## Como personalizar sem ser programador

O arquivo `client-profile.json` contém a parte comercial e comportamental. Nele podem ser alterados nome, personalidade, tom, áreas atendidas, regras, assuntos proibidos, regras de escalonamento e conhecimento aprovado.

Esse arquivo pode ser enviado isoladamente a um ChatGPT ou outra IA para edição. Não é necessário fornecer código-fonte, `.env`, API keys nem a sessão do WhatsApp.

## Segurança das chaves

As chaves de API ficam apenas no `.env` local. Esse arquivo não deve ser enviado ao GitHub. A pasta `data/`, onde fica a sessão do WhatsApp e estado operacional, também deve permanecer privada.

## Autonomia real

A autonomia do aplicativo é operacional: ele consegue atender, selecionar o fluxo de resposta, usar IA, consultar informação, tratar áudio, aplicar regras e escalar casos sem alguém clicar em cada mensagem.

Essa autonomia não significa independência ilimitada. Para funcionar ele precisa de energia, internet, sessão do WhatsApp válida e pelo menos um serviço de IA quando a função exigir IA remota. A Yeshua continua controlando as regras e pode interromper o processo a qualquer momento.

## Operação 24 horas

Se o objetivo for atendimento permanente, o aplicativo deve ficar em um computador dedicado ou servidor/VPS que permaneça ligado. Se a máquina desligar, perder internet ou a sessão do WhatsApp expirar, o atendimento automático para até o serviço ser restabelecido.

## Atualizações contábeis

Leis e regras tributárias mudam. Por isso o aplicativo foi configurado para tratar perguntas sobre impostos, Simples Nacional, MEI, IBS, CBS, prazos e obrigações como conteúdo sensível à data. O fluxo deve consultar fonte oficial atual antes de apresentar uma regra como vigente.

## Quem continua no comando

A função do sistema é aumentar capacidade e velocidade de atendimento. A decisão profissional continua com Anderson, Cristiane e a equipe contábil da Yeshua. O bot é a primeira camada de atendimento, não o substituto da responsabilidade técnica.
