# Federação SPECTER v1 — núcleo local

O PC do operador coordena tarefas, decisões e entregáveis. Cada agente troca
propostas explícitas; o recebimento de uma mensagem nunca autoriza executar seu
conteúdo. Este incremento oferece uma caixa postal local persistente, não uma
rede global pronta ou um serviço autônomo instalado.

## Protocolo implementado

`Core/federation_protocol.py`, somente biblioteca padrão Python:

- Envelope `specter/federation/1`, UUID canônico, remetente e destinatário registrados.
- Tipos `hello`, `proposal`, `result`, `meeting`, `cancel`.
- Objetivo de até 2.000 caracteres, envelope de até 32 KiB, validade máxima de 24 horas.
- Armazenamento SQLite, deduplicação transacional e conflito se o mesmo ID mudar.
- Consulta por destinatário e confirmação explícita de recebimento.
- O payload é dado não confiável. Não existe shell, compra, implantação ou inferência nesse módulo.

Reunião: enviar `meeting` com pauta, evidências e decisão pendente no payload;
cada participante responde com novo ID e referência ao ID original. O coordenador
registra decisão, responsável e critério de aceitação. Essa convenção não é uma
máquina de estados de reuniões. `cancel` é aviso; não interrompe processos externos.

## Exemplo local

Na raiz do repositório, gere um envelope com timestamps atuais:

```powershell
python -c "import json,time,uuid; from pathlib import Path; t=int(time.time()); Path('hello.json').write_text(json.dumps(dict(protocol='specter/federation/1',id=str(uuid.uuid4()),sender='codex',recipient='hermes',kind='hello',objective='Apresentar HUB',created_at=t,expires_at=t+3600,payload={'capabilities':['proposal','review']})),encoding='utf-8')"
python -m Core.federation_protocol submit --db storage/federation.sqlite3 --actor codex --file hello.json
python -m Core.federation_protocol poll --db storage/federation.sqlite3 --actor hermes
python -m Core.federation_protocol ack --db storage/federation.sqlite3 --actor hermes --id UUID_RECEBIDO
```

O recibo `stored` prova persistência, não leitura, execução ou sucesso de uma tarefa.
`poll` pode retornar a mesma mensagem até `ack`; consumidores devem deduplicar o ID.
Mensagens expiradas ficam no histórico e deixam de aparecer em `poll`.

## Linguagem e autoridade

O projeto já possui SPECTER-DSL. O envelope v1 acrescenta uma fronteira estrita de
intercâmbio sem alterar o codec existente. DSL ou JSON não concedem privilégios,
não alteram pesos do modelo e não ampliam cotas. Economia de tokens deve ser
medida com o tokenizador do modelo; não há percentual de economia validado aqui.

## Integração e rede

O adaptador confiável deve determinar a identidade, sem aceitar a declaração do
payload como autenticação. `--actor` é conveniência para o operador local. Acesso
ao banco e aos arquivos deve ficar restrito à conta do operador. Este CLI não deve
ser exposto diretamente à Internet. SHA-256 detecta mudança, não autentica autores.

Próxima etapa: adaptadores específicos ligam esse envelope às integrações reais,
com operações permitidas, teto por tarefa, prazo, recibo e reconciliação. Esses
controles de execução não estão implementados na caixa postal.

Uma futura VPN exige dois nós identificados e autorizados, autenticação, regras
por serviço e teste de revogação. VPN não substitui autenticação de aplicação.
Não foi instalado túnel, criada conta ou contratado servidor nesta versão.

## Validação

`python -m unittest discover -s Core -p "test_*.py"`

Os testes da federação cobrem concorrência, replay divergente, persistência,
expiração, isolamento, remetente incompatível e envelope inválido. O teste HTTP
usa porta efêmera para não colidir com serviços do operador. Testes com mocks
não comprovam disponibilidade de provedores externos.
