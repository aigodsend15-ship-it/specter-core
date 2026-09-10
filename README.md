# SPECTER PLATFORM (v1.1.0 — SOVEREIGN CORE)
Sistema de Orquestracao Autonoma, Execucao Deterministica e Gateway de Inferencia Distribuida.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Architecture: Sovereign](https://img.shields.io/badge/Architecture-Sovereign%20Pull--Only-green.svg)]()

**Criador & Arquiteto:** Guilherme Peralta Novaes  
**Repositorio Oficial:** [https://github.com/aigodsend15-ship-it/specter-core](https://github.com/aigodsend15-ship-it/specter-core)

**Autonomy V2.2 Federation RC1:** implementation and verified release gates are documented in [`docs/AUTONOMY_V2_2_RC1.md`](docs/AUTONOMY_V2_2_RC1.md). The canonical RC1 package is `Core/autonomy_v2_2/` with its regression suite in `Core/tests_autonomy_v2_2/`.

---

## 1. Principios Fundamentais
- **Custo Zero Adicional ($0.00 USD)**: Trava permanente contra cobrancas nao autorizadas em APIs comerciais.
- **Transacoes ACID & Outbox**: Persistencia confiavel sob SQLite WAL com eliminacao de TOCTOU e replay idempotente.
- **Topologia Pull-Only**: Workers locais nao abrem portas TCP/UDP publicas para a Internet.
- **Gateway OpenAI-Compatible (v1.1)**: Servidor HTTP assincrono nativo (`POST /v1/chat/completions`, SSE Streaming, Circuit Breaker e fila anti-starvation).
- **Distribuicao Multi-Plataforma**: Suporte nativo a instalacao via Pip/PyPI (`pyproject.toml`), Docker e Docker Compose.

---

## 2. A Filosofia do Projeto
Consulte [`PHILOSOPHY.md`](PHILOSOPHY.md) para o manifesto integral sobre a fronteira entre modelos e a agência humana ("As três fraturas e a ferramenta como espelho").

---

## 3. Os 6 Modulos Funcionais do Mesh
Consulte [`SPECTER_COLLABORATIVE_MESH.md`](SPECTER_COLLABORATIVE_MESH.md) para detalhes da arquitetura dos 6 agentes:
1. **Kernel Sentinel**: SQLite WAL com `BEGIN IMMEDIATE` e fencing tokens.
2. **Gateway Sentinel**: Servidor HTTP assincrono OpenAI-compatible na porta 8080.
3. **Execution Router**: Roteador tipado com chaveamento dinamico de provedores.
4. **Memory Nexus**: Memoria semantica e dialeto SPECTER-DSL (`:GOAL`, `:PLAN`, `:EXEC`, `:VERIFY`, `:ATTAINED`).
5. **Package Architect**: Empacotamento PEP 517/621 e containerizacao Docker.
6. **Economics Officer**: Politica de custo zero e sustentabilidade do operador ([`MONETIZATION.md`](MONETIZATION.md)).

---

## 4. Instalacao e Uso Rapido

### Instalacao Local
```bash
git clone https://github.com/aigodsend15-ship-it/specter-core.git
cd specter-core
pip install -e .
```

### Inicializacao do Gateway OpenAI-Compatible (v1.1)
```bash
python -m Core.unified_inference_gateway --host 127.0.0.1 --port 8080
```
Endpoints disponiveis:
- `GET http://127.0.0.1:8080/health`
- `GET http://127.0.0.1:8080/v1/models`
- `POST http://127.0.0.1:8080/v1/chat/completions` (JSON e SSE Streaming)

### Execucao via Docker
```bash
docker-compose up -d
```

### Testes Automatizados
```bash
python -m unittest discover -s Core -p "test_*.py"
```

---

## Licenca
Distribuido sob Licenca MIT. Veja [`LICENSE`](LICENSE) para mais informacoes.
