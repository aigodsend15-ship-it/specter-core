# UNIVERSAL LLM INTEGRATION SPECIFICATION (SPECTER CORE v1.1.0)
### Como qualquer LLM ou Framework pode se conectar ao Specter Gateway de qualquer lugar

O Specter opera como um gateway padrão compatível com a especificação OpenAI. Qualquer LLM, framework de agentes (LangChain, AutoGen, CrewAI, LlamaIndex) ou script pode se comunicar diretamente através de HTTP/REST e SSE Streaming.

---

## 1. Conexão via SDK Oficial da OpenAI (Python)

Qualquer aplicação ou agente baseado no SDK da OpenAI pode apontar para o Specter alterando apenas a `base_url`:

```python
from openai import OpenAI

# Inicialização apontando para o nó Specter (local ou IP público/VPS)
client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="specter-sovereign-mesh"  # Qualquer string válida
)

# Chamada síncrona padrão
response = client.chat.completions.create(
    model="specter-sovereign-core",
    messages=[
        {"role": "system", "content": "Você é um nó integrado ao Specter Mesh."},
        {"role": "user", "content": "Executar diagnóstico do sistema."}
    ],
    temperature=0.7
)

print("Resposta:", response.choices[0].message.content)
```

---

## 2. Conexão via Streaming em Tempo Real (SSE)

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="specter")

stream = client.chat.completions.create(
    model="specter-sovereign-core",
    messages=[{"role": "user", "content": "Inicie o fluxo contínuo."}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
```

---

## 3. Conexão Universal via cURL (Terminal / Bash / Scripts)

Qualquer máquina com acesso à rede pode acionar o nó via HTTP:

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-specter-priority: 1" \
  -d '{
    "model": "specter-sovereign-core",
    "messages": [
      {"role": "user", "content": "Verificar nós ativos no cluster"}
    ]
  }'
```

---

## 4. Integração com LangChain / LiteLLM

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8080/v1",
    api_key="specter",
    model="specter-sovereign-core"
)

resultado = llm.invoke("Qual o estado operacional do broker?")
print(resultado.content)
```

---

## 5. Expondo o Nó para a Rede Externa / Internet (Zero Custo)

Para permitir que agentes externos na nuvem (Hugging Face, VPS, outros desenvolvedores) acessem o gateway local sem expor portas de roteador:

```bash
# Opção A: Usando Cloudflare Tunnels (gratuito)
cloudflared tunnel --url http://localhost:8080

# Opção B: Ngrok ou Bore
bore local 8080 --to bore.pub
```
Com isso, a URL pública gerada pode ser fornecida a qualquer modelo externo para consumir a inferência e despachar tarefas para o mesh.

---

## 6. Cloudflare Workers Edge Gateway (v3.0)

O gateway de borda em `Core/deploy/cloudflare/worker.js` provê:
- Proxy reverso de alta performance na rede global da Cloudflare.
- Autenticação obrigatória por Bearer token (`Authorization: Bearer <TOKEN>`).
- Failover automático entre o nó primário (Dynamic IP / Cloudflare Tunnel) e a VPS secundária de backup.
- Streaming SSE bidirecional com zero bufferização e compatibilidade OpenAI.

```bash
cd Core/deploy/cloudflare
npx wrangler deploy
```

---

## 7. Cliente Leve em Python Puro (`mesh_peer_client.py`)

Zero dependências pesadas externas (100% Python Standard Library). Qualquer VPS ou agente remoto (OpenCode, Hermes, Claude, DeepSeek) pode se comunicar via REST e WebSocket:

```python
from mesh_peer_client import SpecterMeshPeerClient

client = SpecterMeshPeerClient(
    base_url="https://edge.specter.mesh",
    fallback_url="https://pintograndao-hermes-bridge.hf.space",
    auth_token="specter-mesh-token-v3",
    node_id="vps-peer-01"
)

# 1. Health check & Models
print("Health:", client.health())
print("Models:", client.list_models())

# 2. Despachar tarefa via SPECTER-DSL
dsl_res = client.execute_dsl(":GOAL #t_01 @Astra act=code.synthesize.v1 timeout=60 key=idem-01")
print("DSL Status:", dsl_res["success"])

# 3. WebSocket em Python Puro (RFC 6455)
ws = client.websocket_connect("/v1/mesh/ws")
ws.send_text("Hello Specter Mesh")
print("WS Echo:", ws.receive_json())
ws.close()
```

---

## 8. OpenCode & Hermes Agent Skill (`deploy/opencode/specter_mesh_skill.md`)

Disponibiliza aos agentes OpenCode e Hermes as instruções formais para interagir com a malha soberana através dos micro-opcodes SPECTER-DSL (`:GOAL`, `:PLAN`, `:EXEC`, `:VERIFY`, `:ATTAINED`, `:MEM`, `:RECONCILE`, `:ESCALATE`).

---

## 9. Prompt de Sistema Universal (`deploy/prompts/UNIVERSAL_AGENT_SYSTEM_PROMPT.md`)

Especificação canônica de system prompt para qualquer LLM externo operar como um nó cooperativo da malha com garantias formais de proveniência criptográfica SHA-256.

---

## 10. Invariante de Segurança e Isolamento Soberano

- **Scripts de Automação de Browser Internos (`chatgpt_sol_bridge.py`, sessões CDP, profiles locais)**:
  Permanecem **100% internos e exclusivos da máquina host soberana**.
  O `BrowserBridgeIsolationGuard` inspeciona todos os payloads de entrada e catálogo de modelos, bloqueando qualquer tentativa de invocação externa ou vazamento de caminhos/sessões do host.
- A malha colabora puramente através de contratos cognitivos, tarefas determinísticas e provas de atingimento SHA-256.

