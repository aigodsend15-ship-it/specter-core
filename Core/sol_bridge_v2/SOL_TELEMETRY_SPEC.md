# SPECTER SOL BRIDGE V2 // ESPECIFICAÇÃO TÉCNICA DE OBSERVABILIDADE E TELEMETRIA
**Documento Técnico Oficial — Versão 2.0**  
**Alvo**: ChatGPT Sol 5.6 / GPT-5 Thinking / Extended Reasoning Models  
**Módulo Implementado**: `C:\specter\Core\sol_bridge_v2\sol_telemetry_engine.py`  
**Data**: 2026-09-06  
**Ambiente**: Windows 10 x64, Python 3.12, Chrome via Named Pipe IPC (`\\.\pipe\codex-browser-use\*`)

---

## 1. Princípios Soberanos & Invariantes de Projeto

A ponte com o modelo de cognição avançada Sol obedece à separação estrita do Specter:
$$\text{Cognição} \neq \text{Execução} \neq \text{Autoridade} \neq \text{Memória}$$

O Sol atua como motor de **Cognição e Raciocínio Profundo**. Ele **não** possui autoridade direta sobre o sistema de arquivos, não retém a verdade canônica de estado fora de checkpoints verificáveis, e não executa código diretamente sem validação criptográfica.

### Invariantes Estritas:
1. **Zero Vazamento de Tokens Privados (Zero-Leak)**:
   - É terminantemente proibido raspar, injetar ou transitar tokens JWT de autorização, cookies de sessão ou credenciais sensíveis via scripts de automação.
   - A telemetria observa exclusivamente elementos visíveis e estruturais da árvore DOM.
2. **Zero Exposição de Portas CDP Não Autenticadas**:
   - A comunicação opera 100% através de Named Pipes do Windows (`\\.\pipe\codex-browser-use\*`) entre processos locais autenticados na sessão do usuário. Nenhuma porta TCP externa (como `--remote-debugging-port=9222`) é aberta na rede.
3. **Integridade de Estado Canônica**:
   - Todo checkpoint de transição satisfaz a Equação Fundamental:
     $$S_t = (G, P, T, M, A, X, C)$$
   - Codificação JSON determinística conforme **RFC 8785** e encadeamento criptográfico com hashes **SHA-256**.
4. **Alta Densidade Semântica (SPECTER-DSL)**:
   - Eliminação de preâmbulos e cortesias verbosas.
   - Economia de 55% a 70% de tokens de contexto através de micro-opcodes (`:GOAL`, `:PLAN`, `:EXEC`, `:VERIFY`, `:ATTAINED`, `:MEM`).

---

## 2. Pilar 1: Detecção da Transição Thinking -> Coding no DOM

### 2.1 O Problema da Abordagem Insegura (JWT / CDP Aberto)
Modelos anteriores ou scripts legados tentavam autenticar via scraping de Bearer tokens no LocalStorage ou cookies de requisições XHR, ou usavam portas CDP remotas expostas. Essa abordagem apresenta graves falhas:
- **Risco de Segurança**: Exposição de credenciais e tokens em logs, arquivos temporários ou processos filhos.
- **Fragilidade Operacional**: Mudanças frequentes na rota `/backend-api/conversation` da OpenAI quebram scripts HTTP diretos.
- **Invisibilidade do Raciocínio Estendido**: APIs padrão muitas vezes truncam ou ocultam o fluxo de *thinking* em tempo real.

### 2.2 Solução: Inspeção Estrutural de Apresentação (Safe DOM Evaluation)
O script `DOM_TELEMETRY_JS` inspeciona o DOM sem ler cookies nem storage. Ele avalia apenas a camada de apresentação através do método `evaluate` no Named Pipe.

#### Máquina de Estados Finita (FSM):
```
 [IDLE] 
    │ (Prompt injetado e submetido)
    ▼
 [SUBMITTED]
    │ (Turno assistente criado no DOM, botão Stop visível)
    ▼
 [THINKING] ──┐ (Thought em progresso, spinner ou container ativo)
    │         │
    │         │ [Gatilho Crítico: Thinking -> Coding]
    │         │ • Container de thought finaliza / colapsa ("Pensou por Xs")
    │         │ • Primeiro bloco de código (<pre><code>) entra em streaming
    ▼         ▼
 [CODING]  [STREAMING_TEXT] (Prosa / texto sem código)
    │         │
    └────┬────┘
         │ (Botão Stop desaparece, botões de cópia renderizados)
         ▼
    [COMPLETED]
         │
         ├─── Se alerta de limite no DOM ──────► [SATURATED]
         └─── Se alerta de erro no DOM ────────► [ERROR]
```

### 2.3 Heurísticas de Seleção DOM
1. **Controles de Fluxo**:
   - Botão de parada: `button[data-testid="stop-button"]`, `button[aria-label*="Stop"]`, `button[aria-label*="Parar"]`.
   - `is_streaming = true` enquanto o botão de parada existir.
2. **Inspeção de Extended Thinking**:
   - Contêineres: `[data-testid*="thought"]`, `[data-testid*="reasoning"]`, `[class*="thought"]`, `summary`, `details`.
   - Botão de status: Botões com rótulo contendo `Pensou por...`, `Thought for...`, `Thinking...`.
   - Extração da Duração: Regex `/(?:thought for|pensou por|thinking for)\s*([0-9.]+)\s*(?:s|segundos)?/i`.
   - Estado Ativo: Presença de spinner SVG (`animate-spin`) ou texto "Pensando..." / "Thinking...".
3. **Inspeção de Blocos de Código**:
   - Elementos `<pre>` e `<code>`.
   - Contagem de blocos: `codeBlocksCount = document.querySelectorAll('pre').length`.
   - Detecção de Linguagens: Classes `language-*`, headers de bloco (`flex items-center justify-between ...`), ou primeira linha de script (`python`, `bash`, `json`, `typescript`).
   - Caracteres Totais de Código: Soma de `innerText` de todos os elementos `<pre>`.
4. **O Gatilho `THINKING -> CODING`**:
   - Ocorre no instante exato em que o estado anterior é `THINKING` e o novo estado avaliado é `CODING`.
   - Dispara o callback `on_transition(record)` e gera um registro imutável:
     ```json
     {
       "from": "THINKING",
       "to": "CODING",
       "ts": 1788743825.12,
       "duration_prev": 8.4,
       "details": {
         "turn_chars": 850,
         "code_blocks_count": 1,
         "languages": ["python"],
         "thought_duration_reported": 8.4,
         "is_thinking_to_coding": true,
         "model_slug": "gpt-5-6-thinking"
       }
     }
     ```
   - O digest SHA-256 desse JSON canônico é salvo no histórico da sessão.

---

## 3. Pilar 2: Detecção de Saturação de Contexto & Geração de Checkpoint

### 3.1 Sinais de Saturação de Contexto
A saturação é monitorada por duas vias complementares:
1. **Sinais Explícitos no DOM**:
   - Banners ou alertas com textos como:
     - *"This conversation is too long. Please start a new conversation."*
     - *"Esta conversa é muito longa. Inicie uma nova conversa para continuar."*
     - *"Conversation length limit reached"* ou *"Message cap reached"*.
   - Área do compositor desabilitada (`#prompt-textarea[disabled]` ou `contenteditable="false"`).
2. **Sinais Heurísticos Quantitativos (`ContextSaturationSentinel`)**:
   - Contagem de turnos acumulados: $N_{turns} \ge 40$.
   - Volume bruto de caracteres: $\sum L_{chars} \ge 350.000$.
   - Estimativa de tokens consumidos:
     $$T_{est} = \left\lfloor \frac{L_{chars}}{3.7} \right\rfloor$$
   - Limiares operacionais:
     - **Soft Warning (75%)**: Alerta preventivo para consolidar tarefas em andamento.
     - **Hard Saturation (90%)**: Disparo obrigatório de transição de thread.

### 3.2 O Checkpoint de Transição ($S_t$)
Ao atingir a saturação, o módulo gera um objeto `TransitionCheckpoint` contendo:
- **Vetor de Estado $S_t = (G, P, T, M, A, X, C)$**:
  - $G$ (*Goals*): Metas ativas, restrições e critérios de aceitação.
  - $P$ (*Plan*): Passos de execução concluídos e pendentes.
  - $T$ (*Tasks*): Tarefas atômicas e identificadores de lease.
  - $M$ (*Messages*): Memória destilada e síntese cognitiva das decisões anteriores.
  - $A$ (*Artifacts*): Manifesto `{caminho_relativo: sha256}` de todos os arquivos relevantes do projeto.
  - $X$ (*External Effects*): Efeitos colaterais executados.
  - $C$ (*Configuration*): Modelos, limites de timeout e especificações de verificadores.
- **Evidências de Testes Aprovados (`approved_tests`)**:
  - `test_id`, `suite_name`, `duration_s`, `passed_assertions`, `failed_assertions`, e `stdout_sha256`.
- **Cadeia Criptográfica Imutável**:
  $$\text{state\_root\_hash} = \text{SHA256}(\text{canonical}(S_t))$$
  $$\text{evidence\_hash} = \text{SHA256}(\text{canonical}(\text{pack}(\text{checkpoint\_id}, \text{seq}, \text{prev\_hash}, \text{state\_root\_hash}, \text{tests})))$$

### 3.3 Prompt de Gênese (Reidratação em Nova Thread)
O método `generate_genesis_prompt(next_intent)` compila imediatamente um prompt ultradenso para semear uma thread virgem (`CONVERSAR_NOVA_THREAD.bat` ou nova aba):
```markdown
# SPECTER STATE HANDOVER // GENESIS THREAD SEED
:GOAL #transition_seed @Sol act=state_rehydrate.v1 aggregate=sol_thread_alpha seq=3
:VERIFY state_root=d56c0493edcffa6bd786380f70abd4678b54dfee95be86e84a530219b5c2531b evidence=3a449254...
:MEM domain=specter_checkpoint checkpoint_id=chk_ee323ee05ffb

## 1. INVARIANT STATE VECTOR S_3
- **Active Goals (G)**: {"objective": "Synthesize security layer", "strict_mode": true}
- **Executed Plan (P)**: Completed 4 steps.
- **Approved Tests Evidence**:
  - [PASS] test_mod_a.py (Assertions: 12, Output Hash: a1b2c3d4...)
- **Validated Artifacts Manifest (A)**:
  - Core/security_vault.py: sha256=f4e5d6c7...

## 2. SOVEREIGN DIRECTIVE
- Cognition != Execution != Authority != Memory.
- Maintain zero hallucinations, absolute typing, and RFC 8785 canonical determinism.

## 3. NEXT OBJECTIVE (:EXEC)
Synthesize next module: sol_state_recovery.py
```

---

## 4. Pilar 3: Templates de Prompt de Alta Densidade Semântica

Para maximizar a eficiência dos modelos de raciocínio profundo (Sol / GPT-5 Thinking) e evitar desperdício com preâmbulos e alucinações, estruturamos quatro templates baseados no dialeto **SPECTER-DSL**:

### 4.1 Template SYNTHESIS (Síntese de Módulo / Algoritmo)
- **Finalidade**: Solicitar a criação de novos módulos de infraestrutura ou lógica de negócio.
- **Invariantes Exigidas**:
  - 100% Python Standard Library (zero bibliotecas externas não autorizadas).
  - Código executável e completo. Zero mocks, zero placeholders, zero comentários `# TODO`.
  - Tipagem estrita (`mypy` / Python 3.10+ typing).
  - Conjunto de testes unitários auto-verificável integrado.

### 4.2 Template REFACTOR (Refatoração de Diff Mínimo)
- **Finalidade**: Modificação pontual de símbolos, correção de bugs e otimizações.
- **Invariantes Exigidas**:
  - Princípio do Menor Diff Correto (*Minimum Correct Diff*).
  - Preservação estrita da retrocompatibilidade das APIs públicas e assinaturas existentes.
  - Proibição de reformatar ou alterar código fora do símbolo alvo.

### 4.3 Template ADVERSARIAL_AUDIT (Auditoria Adversarial e Falsificação)
- **Finalidade**: Análise cética profunda para encontrar vulnerabilidades ocultas.
- **Eixos de Ataque**:
  - Concorrência e bloqueios (race conditions, SQLite WAL lock contention).
  - Dessincronização de estado (stale lease epochs, hashes incorretos).
  - Esgotamento de recursos (leaks de descritores de arquivo, buffers sem limite).
- **Entregável**: Ledger formal de vulnerabilidade + Suíte de testes projetada especificamente para falhar no código sob teste + Patch de correção.

### 4.4 Template CONTEXT_HANDOVER (Handover de Thread)
- **Finalidade**: Inicialização determinística de uma nova conversa após atingir saturação de contexto.
- **Garante**: 100% de reidratação do vetor $S_t$ em menos de 400 tokens de contexto.

---

## 5. Matriz Comparativa de Economia de Tokens

| Modo de Prompt | Caracteres | Tokens Médios Estimados | Redução vs Natural Verboso |
| :--- | :--- | :--- | :--- |
| **Linguagem Natural Tradicional** | ~2.400 | ~650 tokens | Referência (0%) |
| **JSON Estruturado Verboso** | ~1.600 | ~430 tokens | -33.8% |
| **SPECTER-DSL (Sol Prompt Engine)** | **~750** | **~195 tokens** | **-70.0%** |

---

## 6. Validação e Evidência de Execução

O motor foi testado e validado em dois níveis:
1. **Auditoria de Testes Unitários Locais**:
   - Arquivo: `C:\specter\Core\sol_bridge_v2\test_sol_telemetry_engine.py`
   - Resultado: 7 testes executados com sucesso (100% de aprovação) em 0.013 segundos.
   - Validações: Determinismo de codificação canônica, hashes de arquivos binários, máquina de estados FSM, detecção da transição `Thinking -> Coding`, limites de saturação, serialização/desserialização de checkpoints e templates SPECTER-DSL.
2. **Validação em Tempo Real no DOM do ChatGPT**:
   - Conexão ativa via Named Pipe (`CodexBrowserClient` no tab `809727058`).
   - Execução real do payload `DOM_TELEMETRY_JS`:
     - Modelo detectado: `gpt-5-6-thinking`.
     - Blocos de código descobertos: 2 blocos (4.552 caracteres de código).
     - Estado detectado: `COMPLETED`.
     - Zero vazamento de tokens e zero erro de execução.
