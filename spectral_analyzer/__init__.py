"""Spectral Analyzer: ROI-based autofluorescence spectra from spectral (lambda) stacks
(project SpectralAnalysisGUI)."""
__version__ = "1.0.0"

# --------------------------------------------------------------------------- credits
AUTHORS = [  # (name, GitHub account)
    ("Anis Kebbiche", "anis-kebbiche"),
    ("Petia Georgieva", "petia-georgieva"),
    ("Manal Achami", "manal-achami"),
]
COPYRIGHT_YEAR = "2026"
COPYRIGHT = "© " + COPYRIGHT_YEAR + " " + ", ".join(name for name, _ in AUTHORS)
COPYRIGHT_SHORT = "© " + COPYRIGHT_YEAR + " A. Kebbiche, P. Georgieva, M. Achami"


def credits_html() -> str:
    """Authors block of the About window (GitHub links are clickable)."""
    people = "<br>".join(
        f"{name} · <a href='https://github.com/{gh}'>github.com/{gh}</a>" for name, gh in AUTHORS)
    return (f"<p><b>{COPYRIGHT}</b></p><p>{people}</p>"
            "<p>Developed for the M2 course <i>Python Applications for Neuroscience Research</i>, Université Paris Cité, "
            "on projects proposed by SPPIN (Dr Doriane Hazart, Dr Clément Ricard).</p>")
