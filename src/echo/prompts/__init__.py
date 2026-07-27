"""Versioned prompt registry for synthesis.

``build_messages(brief)`` turns a structured grounding brief into chat messages.
Few-shot exemplars are hand-authored (never drawn from Olist or the generated
corpus), so the gold set — which is sampled from the corpus — can never overlap
them. Bump the prompt version in config to produce a new corpus build without
mutating the old.
"""

from __future__ import annotations

_SYSTEM = (
    "Você gera mensagens realistas de clientes de e-commerce brasileiro para um "
    "conjunto de dados SINTÉTICO de pesquisa. Regras:\n"
    "- Escreva como um cliente real escreveria (coloquial, com possíveis erros de digitação).\n"
    "- Baseie TODO fato concreto (categoria de produto, valores, datas, cidade/estado, "
    "parcelas) SOMENTE nos fatos fornecidos. Nunca invente números novos.\n"
    "- Nunca invente dados pessoais reais (nomes, e-mails, CPF, telefone, código de "
    "rastreio). Use marcadores genéricos se precisar.\n"
    "- Varie o tom e a abertura; evite fórmulas repetidas.\n"
    "- Responda APENAS com o texto da mensagem."
)

# A few hand-authored exemplars, rotated by seed. Not from the dataset.
_TICKET_FEWSHOT = [
    ("Shipping & Delivery", "Boa tarde, comprei uma cafeteira dia 03 e a entrega estava prevista "
     "pro dia 12, mas até agora nada. Já passou uma semana do prazo. Podem verificar onde está?"),
    ("Billing & Payment", "Fui cobrado DUAS vezes no cartão pelo mesmo pedido, R$ 189,90 cada. "
     "Preciso do estorno de uma das cobranças com urgência."),
    ("Returns & Refunds", "Cancelei o pedido há 15 dias e até hoje não recebi o reembolso de "
     "R$ 240,00. Ninguém me dá uma posição. Isso é aceitável?"),
    ("Product Quality", "O produto chegou com defeito, não liga de jeito nenhum. Queria uma troca "
     "ou a devolução do valor."),
]
_SURVEY_FEWSHOT = {
    "detractor": "Demorou demais pra chegar e veio amassado. Não recomendo.",
    "passive": "No geral ok, mas a entrega poderia ser mais rápida.",
    "promoter": "Chegou antes do prazo e muito bem embalado, adorei!",
}


def _facts_block(facts: dict) -> str:
    lines = [f"- {k}: {v}" for k, v in facts.items() if v is not None]
    return "\n".join(lines)


def _ticket_user(brief: dict) -> str:
    facts = brief["facts"]
    cat = brief.get("category")
    ex = _TICKET_FEWSHOT[brief["seed"] % len(_TICKET_FEWSHOT)]
    parts = [
        "Gere UMA mensagem de TICKET de suporte (o cliente está entrando em contato; "
        "NÃO é uma avaliação com nota).",
        f"Tema/categoria pretendida: {cat}.",
        f"Tom desejado: {brief['style']['tone']}.",
        f"Comprimento aproximado: {brief['style']['target_length']} caracteres.",
        "Fatos reais do pedido (use apenas estes números/datas):",
        _facts_block(facts),
    ]
    if brief.get("must_include"):
        parts.append("Inclua naturalmente: " + "; ".join(brief["must_include"]) + ".")
    if brief.get("directives"):
        parts.append("Instruções extras: " + " ".join(brief["directives"]))
    parts.append(f"Exemplo de estilo (categoria {ex[0]}): \"{ex[1]}\"")
    return "\n".join(parts)


def _survey_user(brief: dict) -> str:
    facts = brief["facts"]
    sent = brief.get("target_sentiment", "passive")
    ex = _SURVEY_FEWSHOT.get(sent, _SURVEY_FEWSHOT["passive"])
    parts = [
        "Gere UMA resposta de texto para uma pesquisa NPS pós-compra "
        "(pergunta: 'o que podemos melhorar?').",
        f"O cliente é um {sent} (a nota já foi definida). O texto deve combinar com esse sentimento.",
        f"Comprimento aproximado: {brief['style']['target_length']} caracteres.",
        "Fatos reais do pedido:",
        _facts_block(facts),
    ]
    if brief.get("directives"):
        parts.append("Instruções extras: " + " ".join(brief["directives"]))
    parts.append(f"Exemplo de estilo ({sent}): \"{ex}\"")
    return "\n".join(parts)


def build_messages(brief: dict) -> list[tuple[str, str]]:
    user = _ticket_user(brief) if brief["source_type"] == "ticket" else _survey_user(brief)
    return [("system", _SYSTEM), ("user", user)]
