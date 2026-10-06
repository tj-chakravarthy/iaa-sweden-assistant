"""The skills. To add one: one import and one entry here.

iaa run-all goes through them in this order.
"""

from __future__ import annotations

from .core.policy import KNOWN_KINDS
from .core.skill import Skill
from .skills.register.run import SKILL as register

SKILLS: tuple[Skill, ...] = (register,)

# stop at import if a kind is not approved or is claimed by two skills
_claimed = [kind for skill in SKILLS for kind in skill.kinds]
if set(_claimed) - KNOWN_KINDS or len(_claimed) != len(set(_claimed)):
    raise RuntimeError(f"skill kinds {_claimed} must each be in policy.KNOWN_KINDS and belong to one skill")
