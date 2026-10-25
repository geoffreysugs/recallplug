"""Masks personal data in window titles before they are logged.

    'Mail from amy@example.com'   ->  'Mail from [EMAIL]'
    'Call 0912-345-678'           ->  'Call [PHONE]'
    'Card 4111 1111 1111 1111'    ->  'Card [CARD]'
    'Form A123456789'             ->  'Form [ID]'

Over-masking a title is harmless, so the patterns lean towards masking.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 13-19 digits, single spaces or dashes allowed between them
_CARD = re.compile(r"(?<![\d.])\d(?:[ -]?\d){12,18}(?![\d.])")
# +886 912 345 678 / +886-2-2345-6789 / +886(0)912345678
_PHONE_INTL = re.compile(r"\+886[ -]?(?:\(0\)[ -]?)?\d(?:[ -]?\d){7,8}(?!\d)")
# 0912-345-678 / 0912 345 678 / 0912345678
_PHONE_MOBILE = re.compile(r"(?<![\d.])09\d{2}[ -]?\d{3}[ -]?\d{3}(?![\d.])")
# Taiwan national ID: one letter, then 1 or 2, then 8 digits
_TW_ID = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][12]\d{8}(?![0-9])")


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE_INTL.sub("[PHONE]", text)      # before cards: +886 numbers are long
    text = _CARD.sub("[CARD]", text)
    text = _PHONE_MOBILE.sub("[PHONE]", text)
    text = _TW_ID.sub("[ID]", text)
    return text
