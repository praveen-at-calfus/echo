"""Offline stub text renderer (dev/CI/dry-run only — not the production path).

Deterministic, grounded Portuguese templates assembled from the brief facts,
with enough variety to exercise the whole pipeline (including long-item condense
and messy flags) without any API calls. Clearly marked as stub output upstream.
"""

from __future__ import annotations

import random
import string

_TONE = {
    "educado": ("Olá, tudo bem?", "Desde já agradeço."),
    "frustrado": ("Estou muito frustrado.", "Espero uma solução rápida."),
    "irritado": ("Isso é um absurdo!", "Quero uma resposta AGORA."),
    "confuso": ("Não entendi o que aconteceu.", "Alguém pode me explicar?"),
    "formal": ("Prezados,", "Atenciosamente."),
    "urgente": ("URGENTE:", "Preciso de retorno hoje."),
}

_TICKET = {
    "Shipping & Delivery": "Meu pedido {ref} ({cat}) ainda não chegou. A entrega estava prevista para {est} e já se passaram {late} dias de atraso. Moro em {city}/{state}.",
    "Billing & Payment": "Fui cobrado de forma errada no pedido {ref}: o valor foi R$ {val} no {pay}. Preciso que verifiquem essa cobrança.",
    "Returns & Refunds": "Cancelei o pedido {ref} ({cat}) e estou aguardando o reembolso de R$ {refund}. Até agora nada foi devolvido.",
    "Product Quality": "O produto do pedido {ref} ({cat}) chegou com defeito. Não funciona como deveria e quero troca ou devolução.",
    "Customer Service": "Já tentei contato várias vezes sobre o pedido {ref} e ninguém resolve. O atendimento está péssimo.",
    "Availability & Selection": "Fiz o pedido {ref} de {cat} mas foi marcado como indisponível. Vocês vão repor o estoque?",
    "Website/App UX": "Tentei finalizar a compra do pedido {ref} e o site deu erro na etapa de pagamento várias vezes.",
    "Pricing & Value": "O preço de R$ {val} do pedido {ref} ({cat}) está bem acima da concorrência. Não valeu a pena.",
}

_SURVEY = {
    "detractor": "Experiência ruim com o pedido de {cat}. {reason} Não recomendo.",
    "passive": "O pedido de {cat} foi ok, mas dava pra melhorar. {reason}",
    "promoter": "Muito satisfeito com o pedido de {cat}! {reason} Recomendo.",
}
_REASON = {
    "detractor": ("Demorou demais e veio mal embalado.", "Atraso de {late} dias na entrega.", "Produto abaixo do esperado."),
    "passive": ("A entrega poderia ser mais rápida.", "Embalagem simples.", "Nada de excepcional."),
    "promoter": ("Chegou antes do prazo.", "Muito bem embalado.", "Ótimo custo-benefício."),
}

# Seeded variety so retries (and distinct items) diverge in the stub too.
_VARIANTS = (
    "Aguardo um retorno o quanto antes.",
    "Já é a segunda vez que isso acontece.",
    "Podem me ajudar com isso?",
    "Não esperava esse tipo de problema.",
    "Fico no aguardo de uma solução.",
    "Espero que resolvam rápido.",
    "Isso me deixou bastante chateado.",
    "Preciso de uma posição sobre o caso.",
)


def _fmt(tpl: str, facts: dict) -> str:
    """Fill in a Portuguese ticket template with the order's grounding facts, substituting sensible placeholder text for any missing fact."""
    return tpl.format(
        ref=facts.get("order_ref", "XXXXXXXX"),
        cat=facts.get("product_category_en") or "produto",
        est=facts.get("estimated_date") or "a data prevista",
        late=facts.get("lateness_days") if facts.get("lateness_days") is not None else "vários",
        city=facts.get("city") or "minha cidade",
        state=facts.get("state") or "",
        val=facts.get("order_value") if facts.get("order_value") is not None else "—",
        refund=facts.get("refund_amount") if facts.get("refund_amount") is not None else "—",
        pay=facts.get("payment_type") or "cartão",
        reason="",
    )


def _gibberish(rng: random.Random) -> str:
    """Generate a string of random lowercase letter "words" that reads as keyboard-mashed nonsense."""
    return " ".join(
        "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(2, 9)))
        for _ in range(rng.randint(4, 12))
    )


def _spam(rng: random.Random) -> str:
    """Pick one random promotional spam message unrelated to customer support."""
    promos = [
        "PROMOÇÃO IMPERDÍVEL!!! Ganhe R$100 clicando no link agora!!!",
        "Compre seguidores e curtidas baratinho, chama no whats!!!",
        "GANHE DINHEIRO FÁCIL trabalhando de casa, acesse já!!!",
    ]
    return rng.choice(promos)


def render_stub(brief: dict, seed: int) -> str:
    """Render deterministic, seeded Portuguese template text for the given brief (ticket or survey), applying any messy traits (gibberish, spam, sarcasm, off-topic, language switch, length padding) it calls for."""
    rng = random.Random(seed)
    content = brief.get("content", {})
    facts = brief["facts"]

    if brief["source_type"] == "survey" and content.get("score_only"):
        return ""
    if content.get("gibberish"):
        return _gibberish(rng)
    if content.get("spam"):
        return _spam(rng)

    if brief["source_type"] == "ticket":
        cat = brief.get("category") or "Customer Service"
        body = _fmt(_TICKET.get(cat, _TICKET["Customer Service"]), facts)
        tone = brief["style"].get("tone", "educado")
        opener, closer = _TONE.get(tone, _TONE["educado"])
        text = f"{opener} {body} {closer}"
        for phrase in brief.get("must_include", []):
            text += f" ({phrase})"
        if content.get("multi_topic"):
            text += " Além disso, o atendimento por chat não respondeu minhas mensagens."
        if content.get("sarcasm"):
            text = "Ah, que 'maravilha'... " + text + " Parabéns pelo 'ótimo' serviço."
        if content.get("off_topic"):
            text += " Aliás, sábado fui num churrasco em família e choveu o dia todo, uma pena."
    else:
        sent = brief.get("target_sentiment", "passive")
        reason = rng.choice(_REASON[sent])
        reason = reason.format(late=facts.get("lateness_days") or "alguns")
        text = _SURVEY[sent].format(cat=facts.get("product_category_en") or "produto", reason=reason)

    text = f"{text} {rng.choice(_VARIANTS)}"  # seeded variety

    # Language switch (stub approximations).
    lang = brief["style"].get("language", "pt")
    if lang == "en":
        text = f"[EN] My order {facts.get('order_ref')} ({facts.get('product_category_en') or 'item'}) — {text}"
    elif lang == "es":
        text = f"[ES] Mi pedido {facts.get('order_ref')} — {text}"
    elif lang == "pt-en-mix":
        text = f"{text} Please help me, this is unacceptable."

    # Pad long items up to the target length (exercises the condense path).
    target = brief["style"].get("target_length", len(text))
    filler = (
        " Reforço que preciso de uma solução, pois isso já está causando muito transtorno e "
        "prejuízo. Comprei confiando na loja e esperava um tratamento melhor. Aguardo retorno."
    )
    while len(text) < target:
        text += filler
    return text.strip()  # never truncate — raw preservation invariant
