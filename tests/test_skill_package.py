from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "nav-updater" / "SKILL.md"
README = ROOT / "skills" / "nav-updater" / "README.md"


def test_public_nav_skill_is_complete_and_sanitized():
    skill = SKILL.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for section in ("# NAV Updater Skill", "## When to Use", "## Procedure", "## Privacy", "## Verification"):
        assert section in skill
    for text in (skill, readme):
        assert not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        assert "/Volumes/" not in text
        assert "references/data" not in text
        assert "outputs/" not in text
        assert not re.search(r"\b[A-Z]{2,}\d{3,}[A-Z]?\b", text)
    assert "skills/nav-updater" in readme
    assert "docker compose" in skill
