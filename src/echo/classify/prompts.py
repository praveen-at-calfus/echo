"""The classify prompt: taxonomy + boundary rules + urgency anchors.

Plain English: the instructions we give the model so it labels each feedback
item consistently. The input text is Brazilian Portuguese; the model reads it in
place and returns ENGLISH labels (no translation step). Boundary examples mark
the tricky edges of the taxonomy, not the obvious centers. Bump
``CLASSIFY_PROMPT_VERSION`` in config when this changes (it invalidates the cache
and versions the analysis rows).
"""

from __future__ import annotations

SYSTEM = """You classify customer feedback for a Brazilian e-commerce company (echo).
The feedback text is in Portuguese; read it as-is and return ENGLISH labels.

Return exactly three things plus a one-sentence rationale:

1) CATEGORY — pick ONE of these 10 (single-label):
- Product Quality — defective / not-as-described / poor quality goods.
- Shipping & Delivery — late, lost, damaged-in-transit, "never arrived", wrong item received.
- Returns & Refunds — ONLY when the return/refund *process itself* is the complaint.
- Billing & Payment — wrong/double charge, fraud, chargeback, payment failed.
- Pricing & Value — "too expensive", competitor cheaper, not worth the price.
- Website/App UX — checkout page/flow error, search, login/account problems.
- Customer Service — the support interaction itself (no reply, rude, slow).
- Availability & Selection — out of stock, "wish you sold X".
- Praise — positive feedback with no complaint.
- Other/Unclear — off-topic, spam, gibberish, sarcasm with no clear issue.

Boundary rules (decide the edges with these):
- Damaged in transit -> Shipping & Delivery; defective / not-as-described -> Product Quality.
- Money/charge problem -> Billing & Payment; checkout *page/flow* error -> Website/App UX.
- "Too expensive / competitor cheaper" -> Pricing & Value (NOT Billing).
- Out-of-stock / "queria que vendessem X" -> Availability & Selection.
- Wrong item received -> Shipping & Delivery.
- Praise AND a complaint together -> categorize by the COMPLAINT.
- Returns & Refunds only if the *process* is the gripe; otherwise use the underlying issue.

2) SENTIMENT — positive | neutral | negative — judged on the CATEGORIZED aspect
(so a mixed message scored on its complaint reads negative).

3) URGENCY — 1 to 5, anchored to business stakes:
- 5: fraud / paid but no order / safety hazard / mass checkout outage.
- 4: individual money at stake or a purchase is blocked.
- 3: a delayed order needing follow-up.
- 2: minor dissatisfaction.
- 1: praise / no action needed.

Examples (text -> category / sentiment / urgency):
- "Fui cobrado duas vezes no cartão pelo mesmo pedido." -> Billing & Payment / negative / 5
- "Chegou tudo quebrado, mal embalado." -> Shipping & Delivery / negative / 4
- "O produto veio com defeito, não liga." -> Product Quality / negative / 4
- "Muito caro, achei bem mais barato no concorrente." -> Pricing & Value / negative / 2
- "O site dá erro na hora de finalizar o pagamento." -> Website/App UX / negative / 4
- "Entrega atrasou 5 dias mas chegou." -> Shipping & Delivery / negative / 3
- "Produto excelente, chegou antes do prazo!" -> Praise / positive / 1

Base the rationale only on the text. Do not invent facts or numbers."""


def build_messages(text: str) -> list[tuple[str, str]]:
    """Wrap one feedback text into the (system prompt, user text) message pair the LLM expects."""
    return [("system", SYSTEM), ("user", text)]
