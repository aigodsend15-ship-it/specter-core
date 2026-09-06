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
