# SPECTER PLATFORM (v1.0 — SOVEREIGN CORE)
Sistema de Orquestracao Autonoma, Execucao Deterministica e Gateway de Inferencia Distribuida.

---

## 1. Principios Fundamentais
- **Custo Zero Adicional ($0.00 USD)**: Trava permanente contra cobrancas nao autorizadas em APIs comerciais.
- **Transacoes ACID & Outbox**: Persistencia confiavel sob SQLite WAL com eliminacao de TOCTOU e replay idempotente.
- **Topologia Pull-Only**: Workers locais nao abrem portas TCP/UDP publicas.
- **Gateway OpenAI-Compatible**: Endpoint `/v1/chat/completions` com SSE Streaming, Circuit Breaker e anti-starvation.

---

## 2. Estrutura do Codigo
- `Core/autonomous_broker.py`: Broker duravel do Marco 1.
- `Core/mesh_broker_bridge.py`: Unidade de trabalho atomica e outbox com replay.
- `Core/specter_execution_router.py`: Roteador tipado por capacidades e fencing tokens.
- `Core/unified_inference_gateway.py`: Gateway de inferencia com balanceamento e envelhecimento linear.
- `Core/specter_supervisor_247.py`: Loop de supervisao continuo (< 20 MB RAM).
- `Core/specter_cli.py`: Ferramenta de linha de comando para o operador.

---

## 3. Uso Rapido
```bash
# Consultar status operacional
python Core/specter_cli.py status

# Submeter tarefa deterministica
python Core/specter_cli.py submit --text "MINHA_TAREFA"

# Consultar resultado e hash SHA-256
python Core/specter_cli.py results

# Executar testes unitarios
python -m unittest discover -s Core -p "test_*.py"
```
