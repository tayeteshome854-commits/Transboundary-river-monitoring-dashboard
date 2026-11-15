#!/usr/bin/env python3
"""Build the SSGI presentation for the Landslide EAP technical consultation workshop.

    python build_deck.py --dmc /path/to/DMC.pdf [--out FILE.pptx] [--preview DIR]

Every figure is extracted from the SSGI Disaster Monitoring Center (DMC)
presentation PDF, so the deck contains no invented imagery. Claims on the
slides are limited to what the Terms of Reference and the DMC presentation
state; anything else is marked "to be verified" or labelled as proposed.

Requires: python-pptx, pymupdf, pillow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

import deckkit
from deckkit import Deck, Para

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- palette
NAVY, BLUE, GOLD, RED = "#0B2A3C", "#1B5674", "#DD984B", "#E61D30"     # SSGI logo colours
INK, GREY, RULE, LIGHT = "#1F2A33", "#5F6B76", "#D5DBE0", "#F3F6F8"
SLATE = "#7C8A95"
GREEN, PURPLE, CRIMSON = "#2E7D5B", "#6B4E9B", "#B4234A"
VERIFY_BG, VERIFY_TX = "#FFE9A6", "#8A5A00"
BLUE_TINT, GOLD_TINT, GREEN_TINT = "#E8F0F4", "#FBF1E4", "#E7F3EE"
PURPLE_TINT, CRIMSON_TINT = "#EFEBF6", "#FAEBEF"
WHITE = "#FFFFFF"

STYLES = {
    "verify": {"italic": True, "bold": True, "color": VERIFY_TX},
    "accent": {"bold": True, "color": BLUE},
    "bullet_color": GOLD,
}

L, R = 0.55, 12.78            # content margins (inches)
CW = R - L
BOTTOM = 6.72                 # lowest y for slide content

SRC = "Source: SSGI DMC presentation"
FOOTER = ("Space Science and Geospatial Institute  ·  Disaster Monitoring Center      |      "
          "Landslide EAP Technical Consultation Workshop  ·  Addis Ababa, 16–17 November 2026")

# status tags: label, width (in), fill, text colour, outline, dashed
TAGS = {
    "existing": ("EXISTING CAPABILITY", 1.80, BLUE, WHITE, None, False),
    "research": ("RESEARCH OUTPUT", 1.52, GREEN, WHITE, None, False),
    "opportunity": ("OPPORTUNITY", 1.20, PURPLE, WHITE, None, False),
    "proposed": ("PROPOSED", 0.98, WHITE, CRIMSON, CRIMSON, True),
    "verify": ("TO BE VERIFIED", 1.36, VERIFY_BG, VERIFY_TX, None, False),
}


# ---------------------------------------------------------------- assets
def prepare_assets(pdf_path: Path, out: Path) -> dict:
    """Extract the figures used in the deck from the DMC presentation PDF."""
    import pymupdf

    out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))

    def embedded(page_no: int, size: tuple) -> Image.Image:
        page = doc[page_no - 1]
        for info in page.get_images(full=True):
            xref, smask, w, h = info[0], info[1], info[2], info[3]
            if (w, h) == size:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                if pix.colorspace is None or pix.colorspace.n != 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if smask:
                    pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
                mode = "RGBA" if pix.alpha else "RGB"
                return Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        raise SystemExit(f"Figure {size} not found on page {page_no} of {pdf_path.name}. "
                         "Is this the DMC presentation the deck was built from?")

    def save(name: str, im: Image.Image, max_px: int = 2200) -> Path:
        if max(im.size) > max_px:
            k = max_px / max(im.size)
            im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
        if im.mode == "RGBA":
            path = out / f"{name}.png"
            im.save(path, optimize=True)
        else:
            path = out / f"{name}.jpg"
            im.convert("RGB").save(path, quality=90, optimize=True)
        return path

    a = {}
    a["logo"] = save("logo", embedded(1, (1383, 410)), 1400)
    a["ethiopia"] = save("ethiopia_relief", embedded(2, (705, 559)))
    a["fentale"] = save("fentale_interferograms", embedded(3, (2155, 1176)))
    # Gamo field photo: rendered from the page so the "1st / 2nd Landslide" outlines are kept.
    pix = doc[7].get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=pymupdf.Rect(66, 54, 441, 299))
    a["gamo_photo"] = save("gamo_field_photo_annotated",
                           Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    a["gamo_map"] = save("gamo_susceptibility_map", embedded(8, (1431, 1105)))
    a["gamo_photo2"] = save("gamo_field_photo_debris", embedded(8, (1042, 642)))
    a["factors"] = save("conditioning_factors", embedded(9, (1085, 1221)))
    a["training"] = save("training_samples", embedded(9, (1406, 1044)))
    a["importance"] = save("variable_importance", embedded(10, (1582, 1250)))
    a["models"] = save("model_maps", embedded(10, (990, 1499)))
    ens = embedded(11, (2566, 1120))
    a["ens_cont"] = save("ensemble_continuous", ens.crop((10, 60, 1320, 1110)))
    a["ens_class"] = save("ensemble_classified", ens.crop((1320, 110, 2566, 1080)))
    for i, size in enumerate([(1010, 761), (1013, 765), (1006, 767), (641, 538), (834, 660)], 1):
        a[f"insar{i}"] = save(f"hayli_gubbi_insar_{i}", embedded(6, size))
    a["geoportal_events"] = save("geoportal_events", embedded(15, (1280, 638)))
    a["geoportal_hazard"] = save("geoportal_hazard_map", embedded(15, (1280, 654)))
    a["website"] = save("monitoring_website", embedded(16, (1280, 599)))
    a["eqvol"] = save("earthquake_volcano_system", embedded(17, (1280, 695)))
    a["frims"] = save("frims", embedded(18, (1259, 641)))
    # international example images (public domain, USGS); see assets/SOURCES.md
    a["ex_lidar"] = HERE / "assets" / "example_lidar_oso_usgs.jpg"
    a["ex_aerial"] = HERE / "assets" / "example_aerial_oso_usgs.jpg"
    return a


# ---------------------------------------------------------------- shared furniture
def chip(s, x, y, kind, h=0.27):
    label, w, fill, tx, outline, dashed = TAGS[kind]
    s.box(x, y, w, h, fill=fill, line=outline, lw=1.0, dash=dashed, radius=h / 2,
          text=Para(label, track=0.5), size=8.5, color=tx, bold=True, align="c", anchor="m",
          inset=0.0, name=f"Status tag: {label}")
    return w


def chrome(s, A, kicker, title, tags=()):
    """Header, title and footer shared by content slides. Returns the y where content starts."""
    s.box(0, 0, deckkit.SLIDE_W, 0.12, fill=NAVY, name="Top band")
    s.box(deckkit.SLIDE_W - 1.3, 0, 1.3, 0.12, fill=GOLD, name="Top band accent")
    x = R
    for kind in reversed(tags):
        x -= TAGS[kind][1]
        chip(s, x, 0.34, kind)
        x -= 0.1
    s.text(L, 0.36, x - L - 0.1, 0.24, Para(kicker, track=0.7), size=9.5, color=BLUE, bold=True,
           anchor="m", name="Section label")
    lines = round(s.measure(title, CW, size=25, bold=True, ls=0.95) / (25 * 1.15 * 0.95 / 72))
    th = 0.42 if lines <= 1 else 0.82
    s.title(L, 0.70, CW, th, title, size=25, color=NAVY, ls=0.95)
    top = 0.70 + th + 0.22
    # footer
    s.line(L, 6.90, R, 6.90, color=RULE, lw=0.75)
    s.image(A["logo"], L, 6.985, 1.15, 0.34, halign="l", alt="SSGI logo")
    s.text(1.95, 6.985, 9.9, 0.34, FOOTER, size=8, color=GREY, anchor="m", name="Footer")
    s.slide_number(R - 0.6, 6.985, 0.6, 0.34, size=9, color=GREY)
    return top


def caption(s, x, y, w, text, h=0.34):
    s.text(x, y, w, h, text, size=8.5, color=GREY, italic=True, ls=0.98, name="Caption")


def label(s, x, y, w, text, color=BLUE, size=9):
    s.text(x, y, w, 0.22, Para(text, track=0.6), size=size, color=color, bold=True, anchor="m")


def badge(s, x, y, n, d=0.36, fill=BLUE, color=WHITE, size=12):
    s.box(x, y, d, d, fill=fill, shape="oval", text=str(n), size=size, color=color, bold=True,
          align="c", anchor="m", inset=0.0)


# ---------------------------------------------------------------- slides
def slide_title(deck, A):
    s = deck.slide()
    s.box(0, 0, 7.75, deckkit.SLIDE_H, fill=NAVY, name="Title panel")
    s.box(7.75, 0, 0.07, deckkit.SLIDE_H, fill=GOLD, name="Accent")
    s.text(0.7, 0.72, 6.6, 0.26, Para("TECHNICAL CONSULTATION WORKSHOP  ·  LANDSLIDE EARLY ACTION PROTOCOL",
                                      track=0.7), size=9.5, color=GOLD, bold=True, anchor="m")
    s.title(0.7, 1.2, 6.55, 2.2,
            "SSGI’s Geospatial Capabilities and Contributions to the Development of a "
            "Landslide Early Action Protocol in Ethiopia", size=30, color=WHITE, ls=0.98)
    s.box(0.7, 3.78, 0.9, 0.05, fill=GOLD)
    s.text(0.7, 4.02, 6.5, 0.72, [
        Para("Space Science and Geospatial Institute (SSGI)", size=16, bold=True, color=WHITE),
        Para("Disaster Monitoring Center (DMC)", size=13.5, color="#C9D6DE", sb=3),
    ])
    s.text(0.7, 4.98, 6.5, 0.72, [
        Para("**Day 1 session:** Geospatial Technologies for Landslide Mapping and Monitoring",
             size=12, color=WHITE),
        Para("Addis Ababa  ·  16–17 November 2026", size=12, color="#C9D6DE", sb=4),
    ])
    s.text(0.7, 6.3, 6.5, 0.52, [
        Para("PRESENTED BY", size=8.5, bold=True, color=GOLD, track=0.6),
        Para("Taye Teshome", size=14, bold=True, color=WHITE, sb=3),
    ], name="Presenter")
    # right side
    s.image(A["logo"], 8.35, 0.5, 4.45, 1.32, alt="Space Science and Geospatial Institute logo")
    s.image(A["ens_cont"], 8.35, 2.12, 4.45, 3.57, border=RULE,
            alt="National map of continuous landslide susceptibility probability from the ensemble model")
    caption(s, 8.35, 5.76, 4.45,
            "Ensemble landslide susceptibility model, continuous probability (0–1), national scale. "
            "A research output of the SSGI Disaster Monitoring Center. It shows where landslides are "
            "more likely, not when.", h=0.5)
    s.text(8.35, 6.6, 4.45, 0.24, "Prepared in response to Section 5.2 of the workshop Terms of Reference",
           size=9, color=GREY, anchor="m")
    s.notes("""
OPENING (about 1 minute)
- Thank ERCS and partners for convening the workshop. Introduce yourself and the Disaster Monitoring Center.
- This session is SSGI's response to Section 5.2 of the Terms of Reference, which lists five expected contributions from the institute.
- One question runs through the whole presentation: what can SSGI contribute, with its existing capacity and realistic next steps, to the development of Ethiopia's Landslide Early Action Protocol?
- The map on this slide is from our own work: a national ensemble landslide susceptibility model. It tells us where landslides are more likely. It does not tell us when. We return to that distinction on the next slide.

BEFORE PRESENTING
- The title slide shows the presenter name only. Add your position under the name if you want it shown.
- The ToR calls the institute "Ethiopian Space Science and Geo-spatial Institute (ESSGI)"; the logo and the DMC material use "Space Science and Geospatial Institute (SSGI)". This deck uses SSGI throughout.

SOURCES: ToR title, Section 5.2 and Day 1 programme; DMC presentation slide 11 (map).
""")


def slide_context(deck, A):
    s = deck.slide()
    t = chrome(s, A, "WORKSHOP CONTEXT AND PRESENTATION OBJECTIVES",
               "A Landslide EAP needs a shared evidence base. This session sets out SSGI’s part.")
    colw = 5.93
    x2 = R - colw
    label(s, L, t, colw, "WORKSHOP CONTEXT (TERMS OF REFERENCE)")
    s.text(L, t + 0.32, colw, 2.15, [
        Para("Landslides in mountainous, high-rainfall areas cause loss of life, displacement and "
             "damage to infrastructure and livelihoods.", bullet=True),
        Para("The ToR notes limited coordination and consolidated evidence on hazard mapping, "
             "monitoring systems, historical impacts and forecast-based early actions.", bullet=True, sb=7),
        Para("ERCS, with government institutions, academia and technical partners, is initiating a "
             "Landslide EAP. This workshop assesses data, capacities, roles and forecasting "
             "opportunities.", bullet=True, sb=7),
    ], size=12.5, color=INK, ls=1.02)
    label(s, x2, t, colw, "WHAT THIS PRESENTATION DOES")
    items = [
        ("Shows what SSGI has today:", " datasets, Earth observation expertise, the Disaster "
                                       "Monitoring Center and working information systems."),
        ("Shows what is technically possible:", " susceptibility mapping, satellite monitoring, "
                                                "hazard and exposure analysis, with their limits."),
        ("Proposes realistic next steps", " for SSGI, partner institutions and the technical "
                                          "working group."),
    ]
    y = t + 0.34
    for i, (b, rest) in enumerate(items, 1):
        badge(s, x2, y + 0.02, i)
        s.text(x2 + 0.52, y, colw - 0.52, 0.66, f"**{b}**{rest}", size=12.5, color=INK, ls=1.02)
        y += 0.72
    # four questions
    y0 = t + 2.62
    s.line(L, y0, R, y0, color=RULE, lw=0.75)
    label(s, L, y0 + 0.13, CW, "FOUR DIFFERENT QUESTIONS NEED FOUR DIFFERENT PRODUCTS", color=NAVY)
    steps = [
        ("SUSCEPTIBILITY", "Where are landslides more likely?",
         "A static map of terrain and environmental conditions.", "SSGI: core contribution", BLUE),
        ("MONITORING", "What is changing on the ground?",
         "Repeated satellite and field observation of risk areas.", "SSGI: core contribution", BLUE),
        ("FORECASTING", "When may conditions become hazardous?",
         "Rainfall forecasts and thresholds.", "EMI-led; SSGI supplies geospatial inputs", SLATE),
        ("EARLY ACTION", "What is done before impacts occur?",
         "Agreed triggers and pre-planned actions in the EAP.", "ERCS-led with EDRMC; SSGI supplies maps and information", SLATE),
    ]
    w = CW / 4
    yc = y0 + 0.45
    for i, (name, q, desc, role, col) in enumerate(steps):
        x = L + i * w
        s.box(x, yc, w + (0.0 if i == 3 else 0.02), 0.5, fill=col, shape="pentagon" if i == 0 else "chevron",
              text=Para(name, track=0.6), size=11, color=WHITE, bold=True, align="c", anchor="m", adj=0.35)
        tx = x + (0.05 if i == 0 else 0.22)
        s.text(tx, yc + 0.6, w - 0.36, 1.13, [
            Para(q, size=11.5, bold=True, color=NAVY),
            Para(desc, size=10, color=INK, sb=2),
            Para(role, size=9.5, bold=True, color=col if col == BLUE else GREY, sb=3),
        ], ls=1.0)
    s.notes("""
CONTEXT (about 2 minutes)
- The ToR background is clear: landslides are recurrent in mountainous and high-rainfall parts of the country, and the evidence on hazard mapping, monitoring, historical impacts and forecast-based early action is not yet consolidated.
- ERCS is initiating a Landslide Early Action Protocol with government, academia and technical partners. This workshop is the first step: assess what data and capacity exist and agree how to work together.
- Our presentation has three aims: show what SSGI has today, show what is technically possible and where the limits are, and propose realistic next steps.

THE FOUR QUESTIONS (keep this on screen; it frames the rest of the talk)
- Susceptibility: WHERE are landslides more likely? A static map built from terrain and environmental conditions.
- Monitoring: WHAT is changing? Repeated observation of known risk areas.
- Forecasting: WHEN may conditions become hazardous? This depends mainly on rainfall forecasts and thresholds, which is EMI's mandate.
- Early action: WHAT is done before impacts? Triggers and pre-agreed actions in the EAP, led by ERCS with EDRMC.
- SSGI's core contribution is to the first two. We support the last two with maps and geospatial information; we do not replace the institutions that lead them.

SOURCES: ToR Sections 1 (Background), 2 (Purpose), 5.3, 5.5, 5.6.
""")


def slide_mandate(deck, A):
    s = deck.slide()
    t = chrome(s, A, "SSGI’S MANDATE UNDER ToR SECTION 5.2",
               "The ToR asks SSGI for five contributions. This presentation answers each in turn.")
    cards = [
        ("Present available geospatial datasets relevant to landslide risk", "Slides 5 and 9"),
        ("Share expertise on remote sensing and earth observation technologies", "Slide 6"),
        ("Demonstrate opportunities for landslide susceptibility mapping", "Slides 7 and 8"),
        ("Discuss options for continuous monitoring of risk areas using satellite technology", "Slide 10"),
        ("Support hazard mapping and exposure analysis processes", "Slide 11"),
    ]
    gap = 0.18
    w = (CW - 4 * gap) / 5
    h = 2.95
    for i, (txt, ref) in enumerate(cards):
        x = L + i * (w + gap)
        s.box(x, t, w, h, fill=LIGHT, name=f"Contribution {i + 1}")
        s.box(x, t, w, 0.06, fill=BLUE)
        s.text(x + 0.16, t + 0.2, 0.8, 0.55, str(i + 1), size=30, color=GOLD, bold=True, anchor="m")
        s.text(x + 0.16, t + 0.82, w - 0.3, 1.25, txt, size=12.5, color=NAVY, bold=True, ls=1.0)
        s.text(x + 0.16, t + h - 0.38, w - 0.3, 0.26, ref, size=10, color=BLUE, bold=True, anchor="m")
    s.text(L, t + h + 0.1, CW, 0.26,
           "Supporting capacity:  Disaster Monitoring Center (slide 4)   ·   information systems "
           "(slides 12–13)   ·   collaboration (slide 14)   ·   gaps and next steps (slides 15–16)",
           size=10, color=GREY, anchor="m")
    # legend
    yl = t + h + 0.55
    s.line(L, yl, R, yl, color=RULE, lw=0.75)
    label(s, L, yl + 0.13, CW, "HOW TO READ THIS PRESENTATION: EACH SLIDE IS TAGGED", color=NAVY)
    defs = [
        ("existing", "In place at SSGI today, as shown in the DMC source material."),
        ("research", "Results of SSGI studies. Not an operational service."),
        ("opportunity", "A technically feasible monitoring or analysis option that still needs piloting."),
        ("proposed", "A future system component. It does not exist yet."),
        ("verify", "Detail or metadata that SSGI must confirm before it is relied on."),
    ]
    for i, (kind, txt) in enumerate(defs):
        x = L + i * (w + gap)
        chip(s, x, yl + 0.5, kind)
        s.text(x, yl + 0.86, w - 0.05, 0.8, txt, size=10, color=INK, ls=1.0)
    s.notes("""
MANDATE (about 1 minute)
- Section 5.2 of the ToR lists five expected contributions from SSGI. The wording on the cards is taken directly from the ToR.
- The presentation follows the same order: datasets, remote sensing expertise, susceptibility mapping, satellite monitoring, and hazard and exposure analysis. The slide numbers on each card show where each is addressed.
- Around those five we also describe the capacity that makes them deliverable: the Disaster Monitoring Center and our experience building disaster information systems.

HOW TO READ THE TAGS
- Blue "Existing capability": in place today.
- Green "Research output": a result from our studies, not an operational service.
- Purple "Opportunity": technically feasible, but needs piloting and validation.
- Dashed red "Proposed": does not exist yet.
- Yellow "To be verified": a detail SSGI still has to confirm. We have kept these visible on purpose so that partners know exactly what is confirmed and what is not.

SOURCE: ToR Section 5.2.
""")


def slide_dmc(deck, A):
    s = deck.slide()
    t = chrome(s, A, "INSTITUTIONAL CAPACITY AND THE DISASTER MONITORING CENTER",
               "The Disaster Monitoring Center is SSGI’s hub for multi-hazard monitoring and information",
               tags=("existing",))
    # left visual
    s.image(A["ethiopia"], L, t + 0.9, 2.45, 2.0, alt="Relief image of Ethiopia from the DMC overview slide")
    s.text(L, t + 3.0, 2.45, 0.6, [
        Para("Disaster Monitoring Center", size=12.5, bold=True, color=NAVY, align="c"),
        Para("Landslides are within its stated national hazard-monitoring scope.", size=9.5,
             color=GREY, align="c", sb=3),
    ], ls=1.0)
    caption(s, L, 6.3, 2.45, f"{SRC}, slide 2.", h=0.34)
    x1, w1 = 3.3, 4.5
    x2, w2 = 8.18, 4.6
    label(s, x1 + 0.5, t, w1 - 0.5, "DMC FUNCTIONS, AS STATED BY THE CENTER")
    label(s, x2, t, w2, "HOW EACH COULD SUPPORT THE LANDSLIDE EAP", color=PURPLE)
    rows = [
        ("National hazard monitoring",
         "Tracks natural and human-made hazards across Ethiopia, including floods, landslides, "
         "earthquakes, volcanic activity, wildfires and industrial accidents.",
         "Landslide information management",
         "An institutional home for a national landslide inventory and map products."),
        ("Early warning systems",
         "Provides timely alerts and forecasts to support government agencies, local authorities "
         "and communities.",
         "Communication of hazard information",
         "A channel for landslide hazard information once triggers are agreed with the mandated institutions."),
        ("Data integration and analysis",
         "Combines satellite imagery, geospatial data, ground-based sensors and field observations.",
         "Technical analysis",
         "Susceptibility, deformation and exposure analysis that brings in partner datasets."),
        ("Post-disaster assessment and research",
         "Assesses damage immediately after events and conducts research to improve monitoring "
         "and prediction.",
         "Inventory updating and model improvement",
         "Post-event mapping, as after the Gamo Zone incident, feeds the inventory and the models."),
        ("Collaboration and networking",
         "Works with stakeholders; disseminates bulletins, reports, dashboards and awareness materials.",
         "Monitoring coordination and dissemination",
         "Bulletins and dashboards for the EAP technical working group and users."),
    ]
    y = t + 0.32
    rh = (BOTTOM - y - 4 * 0.08) / 5
    for i, (f, fd, a, ad) in enumerate(rows, 1):
        badge(s, x1, y + (rh - 0.36) / 2, i)
        s.box(x1 + 0.5, y, w1 - 0.5, rh, fill=BLUE_TINT,
              text=[Para(f, size=11, bold=True, color=NAVY), Para(fd, size=9.5, color=INK, sb=1.5)],
              inset=(0.12, 0.07, 0.1, 0.04), anchor="m", ls=0.98)
        s.line(x1 + w1 + 0.06, y + rh / 2, x2 - 0.06, y + rh / 2, color=PURPLE, lw=1.25, arrow=True)
        s.box(x2, y, w2, rh, fill=PURPLE_TINT,
              text=[Para(a, size=11, bold=True, color=PURPLE), Para(ad, size=9.5, color=INK, sb=1.5)],
              inset=(0.12, 0.07, 0.1, 0.04), anchor="m", ls=0.98)
        y += rh + 0.08
    s.notes("""
THE DMC (about 3 minutes)
- The Disaster Monitoring Center is the unit within SSGI that deals with hazards. The left column lists its five functions exactly as the Center states them.
- Note function 1: landslides are named explicitly in the DMC's hazard-monitoring scope, alongside floods, earthquakes, volcanic activity and wildfires.
- The right column is our reading of how each function could serve the Landslide EAP. These are potential applications, shown in purple, not services already running for landslides.

IMPORTANT POSITIONING
- Function 2 is the DMC's stated early-warning role across hazards. We are NOT claiming that an operational nationwide landslide early warning service exists. For landslides, the DMC could become a channel for hazard information once thresholds and triggers have been agreed with EMI, EDRMC and ERCS.
- The DMC's stated future directions also include developing and validating AI/ML models for multi-hazard prediction, including landslides (DMC slide 20). The landslide work shown later is part of that direction.

SOURCE: DMC presentation slide 2 (functions) and slide 20 (focus areas and future directions).
""")


def slide_datasets(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 1 — AVAILABLE GEOSPATIAL DATASETS",
               "Geospatial data for landslide risk: what SSGI holds or already uses",
               tags=("existing", "verify"))
    rows = [
        ["Dataset", "Why it matters for landslide assessment", "Status and evidence"],
        ["**Optical satellite imagery** (Sentinel-2 and Landsat class)",
         "Mapping landslide scars, land-cover change and inventory updates",
         "Open-access missions. [[SSGI archive and holdings to be verified]]"],
        ["**Radar imagery** (Sentinel-1 SAR; NISAR)",
         "Ground-deformation detection; works through cloud",
         "<<In use:>> DMC InSAR work at Fentale, Erta Ale and Hayli Gubbi"],
        ["**DEM and terrain derivatives:** slope, aspect, elevation, drainage density, distance from stream",
         "Core conditioning factors; drainage and flow paths",
         "<<In use:>> DMC national model. Figure label reads SRTM 30 m"],
        ["**LiDAR**",
         "High-resolution bare-earth terrain; scarps, deposits and cracks",
         "Available at SSGI. [[Coverage, dates and point density to be verified]]"],
        ["**Aerial photography**",
         "Feature interpretation; multi-date historical inventory",
         "Available at SSGI. [[Coverage, dates and scale to be verified]]"],
        ["**Lithology and faults**",
         "Material strength and structural weakness",
         "<<In use:>> DMC national model. [[Data source to be verified]]"],
        ["**Land use / land cover**",
         "Vegetation cover and slope modification",
         "<<In use:>> DMC national model. [[Data source to be verified]]"],
        ["**Rainfall, soil type, soil moisture**",
         "Triggering and preparatory conditions",
         "<<In use:>> DMC national model. [[Source to be verified]]; EMI and MoWE are the mandated providers"],
        ["**Landslide inventory**",
         "Training and validating models; trigger analysis",
         "478 locations used by DMC. [[Source, dates and completeness to be verified]]"],
        ["**Roads, administrative boundaries, disaster event records**",
         "Exposure analysis and reporting by administrative unit",
         "<<In use:>> model inputs and Geoportal event records"],
    ]
    iw = 3.55
    tw = CW - iw - 0.3
    cols = [2.55, 2.55, tw - 5.1]
    yb = s.table(L, t, cols, rows, size=10, color=INK, header_fill=NAVY, header_size=10,
                 rule=RULE, pad=(0.09, 0.052), ls=0.98, name="Dataset–purpose matrix")
    x = R - iw
    _, iy, _, ih = s.image(A["factors"], x, t, iw, iw * 1221 / 1085, valign="t", border=RULE,
                           alt="Twelve national-scale maps of landslide conditioning factors: aspect, "
                               "elevation, drainage density, distance from faults, land use/land cover, "
                               "lithology, annual precipitation, distance from road, slope, soil moisture, "
                               "soil type and distance from stream")
    caption(s, x, iy + ih + 0.06, iw,
            "Twelve national-scale factor layers assembled for the DMC landslide model: aspect, elevation, "
            f"drainage density, distance from faults, land use/land cover, lithology, annual precipitation, "
            f"distance from road, slope, soil moisture, soil type, distance from stream. {SRC}, slide 9.",
            h=0.9)
    s.notes(f"""
DATASETS (about 3 minutes)
- This is the dataset–purpose matrix the ToR asks for. Read it by the third column:
  "In use" means the dataset appears in work shown in the DMC presentation, so we know SSGI already works with it.
  Yellow "to be verified" means SSGI has to confirm the detail before partners rely on it.
- The figure on the right is the strongest evidence on this slide: twelve factor layers already assembled at national scale for the DMC landslide model. The figure label on the training-sample map records the DEM as SRTM 30 m.
- LiDAR and aerial photography get their own slide (slide 9) because they are SSGI's most distinctive high-resolution resource.
- Rainfall, soil and soil moisture layers were used in the model, but EMI and MoWE are the mandated providers of authoritative data. Part of the purpose of this workshop is to agree which source to use.
- The Disaster Management Geoportal already holds disaster event records by type, including landslides (the screenshot on slide 12 shows a landslide count of 115). EDRMC remains the authority for impact records.

DO NOT STATE
- Coverage, acquisition dates, resolution, point density, formats or quantities for any SSGI holding unless you have confirmed them. None of these is given in the source documents.

VERIFY BEFORE PRESENTING
- LiDAR and aerial photography: coverage, dates, point density / scale, access conditions.
- Source of each model input layer (lithology, faults, LULC, soil, soil moisture, precipitation, roads).
- Source and dating of the landslide inventory. Note that the caption says 478 locations while the legend of the training-sample figure says 473.

SOURCES: DMC presentation slides 3, 5, 6 (InSAR), 9 (factor layers, training samples), 10 (variables), 15 (Geoportal).
""")
    return yb


def slide_rs(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 2 — REMOTE SENSING AND EARTH OBSERVATION EXPERTISE",
               "SSGI applies optical, radar and InSAR methods in its hazard monitoring work",
               tags=("existing",))
    items = [
        ("Optical remote sensing",
         "Sentinel and Landsat class imagery for land-surface mapping and change."),
        ("SAR and InSAR",
         "Sentinel-1 interferometry to detect and monitor ground-surface deformation; Sentinel-1 "
         "and NISAR radar data compared by DMC."),
        ("Digital image processing and GIS",
         "Spatial analysis, terrain derivatives and overlay of hazard and exposure layers."),
        ("Multi-temporal analysis",
         "Time series of repeat acquisitions and change detection."),
        ("Data integration",
         "Satellite observations combined with field observations, ground-based sensors and "
         "datasets from other institutions."),
        ("Machine learning and AI",
         "Random Forest, XGBoost and Logistic Regression models for spatial hazard assessment."),
    ]
    lw_ = 4.95
    y = t
    rh = (BOTTOM - t) / 6
    for i, (h, d) in enumerate(items):
        if i:
            s.line(L, y, L + lw_, y, color=RULE, lw=0.75)
        s.box(L, y + 0.12, 0.06, rh - 0.24, fill=GOLD)
        s.text(L + 0.2, y + 0.09, lw_ - 0.2, rh - 0.12, [
            Para(h, size=12, bold=True, color=NAVY),
            Para(d, size=10, color=INK, sb=2),
        ], ls=1.0)
        y += rh
    x = L + lw_ + 0.4
    iw = R - x
    _, iy, _, ih = s.image(A["fentale"], x, t, iw, iw * 1176 / 2155, valign="t", border=RULE,
                           alt="Eight Sentinel-1 interferograms of the Fentale area between September 2024 and April 2025")
    caption(s, x, iy + ih + 0.06, iw,
            "Interferograms from InSAR processing with SARPROAZ software and radar data, Fentale area, "
            f"12 September 2024 to 28 April 2025. {SRC}, slide 3.", h=0.34)
    yc = iy + ih + 0.47
    s.box(x, yc, iw, BOTTOM - yc, fill=GOLD_TINT)
    s.box(x, yc, 0.07, BOTTOM - yc, fill=GOLD)
    s.text(x + 0.22, yc, iw - 0.36, BOTTOM - yc, [
        Para("**What this shows, and what it does not.** SSGI turns repeat-pass radar data into "
             "deformation products, shown here for volcanic and seismic unrest. The same processing "
             "applies to landslides, but small slopes, vegetation and steep terrain make it harder: "
             "pilot testing is needed.", size=10.2, color=INK),
    ], anchor="m", ls=1.02)
    s.notes("""
REMOTE SENSING EXPERTISE (about 3 minutes)
- Six method areas, each of which appears in work shown in the DMC presentation.
- Optical: Sentinel and Landsat class imagery. The DMC monitoring website lists land surface temperature, soil moisture and rainfall anomaly monitoring using MODIS and Sentinel satellites.
- SAR and InSAR: the figure shows eight interferograms over the Fentale area between September 2024 and April 2025, processed with SARPROAZ software. The fringes show ground deformation associated with the seismic and magmatic unrest. DMC has also compared Sentinel-1 with NISAR radar data (wrapped phase, unwrapped phase and coherence) and has applied geodetic techniques to assess earthquake effects on Kesem Dam.
- The DMC website describes its satellite geodesy work as InSAR and GPS measurements to track ground deformation across volcanic, seismic and landslide-prone zones.
- Machine learning: Random Forest, XGBoost and Logistic Regression, used in the landslide model we show next, and in flood susceptibility mapping for the Awash basin.

BE CLEAR ABOUT THE LIMIT
- The InSAR examples are for volcanic and seismic deformation, which produce large, coherent signals over wide areas. Landslides are smaller, often on vegetated steep slopes. The skills transfer; the result is not guaranteed. That is why we propose pilot sites later.

SOURCES: DMC presentation slides 3, 4, 5, 6, 12, 14, 16.
""")


def slide_gamo(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 3 — LANDSLIDE SUSCEPTIBILITY MAPPING",
               "Landslide susceptibility assessment: from the Gamo Zone incident to a national training set",
               tags=("research",))
    wa, wb, wc, gap = 3.3, 4.2, 4.37, 0.18
    xa, xb = L, L + wa + gap
    xc = xb + wb + gap
    mh = 3.244
    # A: photo + what the source shows
    _, _, _, ph = s.image(A["gamo_photo"], xa, t, wa, 2.2, valign="t",
                          alt="Field photograph of a hillside in Gamo Zone with two landslide scars outlined "
                              "and labelled 1st Landslide and 2nd Landslide")
    caption(s, xa, t + ph + 0.05, wa,
            f"Gamo Zone landslide site; two failures outlined. {SRC}, slide 8.")
    ya = t + ph + 0.52
    label(s, xa, ya, wa, "WHAT THE SOURCE SHOWS")
    s.text(xa, ya + 0.28, wa, BOTTOM - ya - 0.28, [
        Para("Field assessment of the recent incident: two adjacent failures documented on site.", bullet=True),
        Para("A zone-wide susceptibility index in five classes. The legend title indicates an "
             "InSAR-informed index.", bullet=True, sb=4),
        Para("[[Incident date, impacts and method details are not in the source slide: to be confirmed.]]",
             bullet=True, sb=4),
    ], size=10, color=INK, ls=1.0)
    # B: Gamo map
    s.image(A["gamo_map"], xb, t, wb, mh, valign="t", border=RULE,
            alt="Landslide susceptibility map of Gamo Zone in five classes from very low to very high")
    caption(s, xb, t + mh + 0.05, wb,
            "Landslide susceptibility assessment of the recent Gamo Zone landslide incident. Five classes, "
            f"Very Low to Very High; legend ‘Landslide Index_InSAR’. {SRC}, slide 8.", h=0.5)
    yf = t + mh + 0.62
    fh = BOTTOM - yf
    s.box(xb, yf, wb, fh, fill=GREEN_TINT)
    s.text(xb + 0.16, yf, wb - 0.3, fh, [
        Para("ZONE SCALE", size=9, bold=True, color=GREEN, track=0.6),
        Para("Shows which parts of a known hotspot zone are most susceptible, to guide where "
             "detailed investigation should go first.", size=10, color=INK, sb=2),
    ], anchor="m", ls=1.0)
    # C: training samples
    s.image(A["training"], xc, t, wc, mh, valign="t", border=RULE,
            alt="National map of landslide and non-landslide training samples overlaid on elevation")
    caption(s, xc, t + mh + 0.05, wc,
            "Landslide training samples overlaid on DEM, Ethiopia, national scale (DEM: SRTM 30 m). "
            f"{SRC}, slide 9.", h=0.5)
    s.box(xc, yf, wc, fh, fill=GREEN_TINT)
    s.text(xc + 0.16, yf, 1.2, fh, "478", size=32, bold=True, color=GREEN, anchor="m")
    s.text(xc + 1.32, yf, wc - 1.45, fh, [
        Para("landslide locations used to train the machine-learning models.", size=10, bold=True, color=NAVY),
        Para("[[Figure legend reads 473 landslide and 561 non-landslide samples: count to be confirmed.]]",
             size=8.8, sb=2),
    ], anchor="m", ls=0.98)
    s.notes("""
GAMO ZONE CASE STUDY (about 3 minutes)
- This is SSGI's own institutional example of landslide susceptibility work.
- Left: a field photograph from the Gamo Zone site. Two adjacent failures are outlined and labelled "1st Landslide" and "2nd Landslide" in the source slide.
- Centre: the DMC's "Landslide Susceptibility Assessment of the Recent Gamo Zone Landslide Incident". The zone is classified in five classes from Very Low to Very High. The legend title reads "Landslide Index_InSAR", which indicates that InSAR information was used in the index. Woreda and town names on the map include Chencha, Dita, Boreda, Kucha, Bonke, Kemba, Geresse and Arba Minch.
- Right: the national training-sample map. The DMC caption states that 478 landslide locations were used to train the machine-learning models. The samples are plotted on an SRTM 30 m elevation model with level-2 administrative boundaries.

PLEASE NOTE BEFORE PRESENTING
- The caption on the DMC slide says 478 landslide locations. The legend printed inside the same figure says "Landslide (n=473)" and "Non-landslide (n=561)". The headline number on this slide follows the caption; confirm which count is correct and correct the slide if needed.
- The DMC slide does not give the date of the Gamo incident, its impacts, or the exact method behind the zone map. Do not state these unless you have them from the study itself.
- The modelling shown on the next slide is at national scale. The Gamo Zone map is a zone-level assessment. Keep the two distinct when speaking.

SOURCES: DMC presentation slides 8 and 9.
""")


def slide_ml(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 3 — MACHINE LEARNING AND PREDICTIVE MODELLING",
               "Machine learning turns conditioning and triggering factors into national susceptibility maps",
               tags=("research",))
    ih = 3.6
    w1, w2, w3 = ih * 1582 / 1250, ih * 990 / 1499, ih * 1246 / 970
    gap = (CW - w1 - w2 - w3) / 2
    x1, x2, x3 = L, L + w1 + gap, L + w1 + w2 + 2 * gap
    s.image(A["importance"], x1, t, w1, ih, border=RULE,
            alt="Bar charts of variable importance for Random Forest, XGBoost and Logistic Regression "
                "with five-fold cross-validation, and a combined normalised importance chart")
    s.image(A["models"], x2, t, w2, ih, border=RULE,
            alt="Continuous and classified landslide susceptibility maps for Random Forest, XGBoost, "
                "Logistic Regression and Ensemble models")
    s.image(A["ens_class"], x3, t, w3, ih, border=RULE,
            alt="National classified landslide susceptibility map from the ensemble model")
    yc = t + ih + 0.05
    caption(s, x1, yc, w1, f"Variable importance, all models, 5-fold cross-validation. {SRC}, slide 10.")
    caption(s, x2, yc, w2 + 0.3, "Continuous and classified maps from four models. Slide 10.")
    caption(s, x3, yc, w3, f"Ensemble model, classified susceptibility, national scale. {SRC}, slide 11.")
    ys = yc + 0.42
    facts = [
        ("Inputs", NAVY,
         "478 landslide locations and 12 factor layers: static conditioning (preparatory) factors "
         "and dynamic triggering factors."),
        ("Variable importance", NAVY,
         "Slope ranks first in Random Forest and XGBoost; annual precipitation ranks first in "
         "Logistic Regression."),
        ("Model outputs", NAVY,
         "Each model gives a continuous probability (0–1) and a classified map. The ensemble is "
         "presented as the best-model national map."),
        ("Read it correctly", CRIMSON,
         "It shows where landslides are more likely, not when. It is not a forecast or an early "
         "warning system. [[Accuracy metrics to be added.]]"),
    ]
    g = 0.2
    w = (CW - 3 * g) / 4
    for i, (h, col, txt) in enumerate(facts):
        x = L + i * (w + g)
        s.box(x, ys, w, 0.04, fill=col)
        s.text(x, ys + 0.1, w, BOTTOM - ys - 0.1, [
            Para(h, size=10.5, bold=True, color=col),
            Para(txt, size=9.5, color=INK, sb=1.5),
        ], ls=0.98)
    s.notes("""
MACHINE LEARNING (about 4 minutes)
- Inputs. 478 landslide locations (plus non-landslide samples) and twelve factor layers. The DMC groups the layers as static conditioning, or preparatory, factors and dynamic triggering factors. The twelve shown are: slope, elevation, aspect, drainage density, distance from stream, distance from faults, lithology, soil type, land use/land cover, distance from road, annual precipitation and soil moisture.
- Why these factors matter, in general terms: slope and elevation control gravitational stress and local climate; lithology, faults and soil type control material strength; drainage and streams undercut and saturate slopes; land cover affects root reinforcement and infiltration; roads cut and load slopes; rainfall and soil moisture raise pore pressure and are the usual triggers.
- Left figure: variable importance under 5-fold cross-validation for three algorithms. Slope is the top-ranked variable in Random Forest and XGBoost. Annual precipitation is top-ranked in Logistic Regression, with slope second. Elevation, lithology, soil moisture and distance from road also rank highly.
- Centre figure: each of four models (Random Forest, XGBoost, Logistic Regression and an Ensemble) produces a continuous susceptibility probability from 0 to 1 and a classified map.
- Right figure: the ensemble classified map, which the DMC presents under the caption "Best Model National Landslide Prediction Map". High and very high classes concentrate in the highlands and along the rift margins.

WHAT THIS IS NOT
- A susceptibility map answers WHERE. It does not say WHEN a landslide will occur. It is not a real-time forecast and not an operational early warning system. For an EAP it is the spatial layer on which rainfall forecasts and thresholds from EMI would be applied.

VERIFY BEFORE PRESENTING
- Model performance (for example AUC or accuracy) is not shown in the DMC slides. Add the figures from the study if available; do not quote any from memory.
- Confirm that the "best model" is the ensemble.
- Class thresholds differ between two figures: the multi-model figure title gives 0.25 / 0.50 / 0.75 / 0.85, while the map legends give 0.30 / 0.60 / 0.80 / 0.92. Confirm which set applies.
- Vegetation indices such as NDVI are not shown as a separate factor layer in the source; land use/land cover is. Do not claim NDVI was used unless confirmed.
- The precipitation layer is annual precipitation, and the temporal basis of the soil moisture layer is not stated. They describe typical wetness, not the rainfall of a particular event.

SOURCES: DMC presentation slides 9, 10 and 11.
""")


def slide_lidar(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTIONS 1 AND 3 — HIGH-RESOLUTION ASSESSMENT",
               "LiDAR and aerial photography show terrain detail that satellites miss",
               tags=("existing", "verify"))
    steps = ["LiDAR and aerial imagery", "High-resolution terrain interpretation",
             "Landslide feature mapping and inventory", "Field validation",
             "Improved susceptibility and hazard assessment"]
    w = CW / 5
    for i, txt in enumerate(steps):
        s.box(L + i * w, t, w + (0.0 if i == 4 else 0.02), 0.66, fill=NAVY if i == 4 else BLUE,
              shape="pentagon" if i == 0 else "chevron", text=txt, size=10.5, color=WHITE, bold=True,
              align="c", anchor="m", adj=0.32, ls=0.98)
    yc = t + 0.86
    yv = 6.1
    ch = yv - 0.16 - yc
    g = 0.2
    cw_ = (CW - g) / 2
    pad = 0.18
    cap_h = 0.32
    ih = ch - 0.76 - 0.06 - cap_h - 0.1
    iw = 3.45
    cards = [
        ("LiDAR", "ex_lidar", [
            "Bare-earth terrain models, including under partial vegetation cover.",
            "Slope, hillshade and roughness reveal scarps, deposits, cracks and old landslide bodies.",
            "A reference for checking satellite-based mapping.",
        ], "LiDAR terrain view of the 2014 Oso landslide, Washington, USA, coloured by elevation",
           "LiDAR terrain view of the 2014 Oso landslide, Washington, USA."),
        ("Aerial photography", "ex_aerial", [
            "Visual and stereo interpretation of landslide features and land use.",
            "Multi-date photographs help identify and date past landslides.",
            "A detailed base for field mapping and for locating exposed buildings and roads.",
        ], "Oblique aerial photograph of the 2014 Oso landslide, Washington, USA, showing the source area and path",
           "Oblique aerial photograph of the same landslide."),
    ]
    for i, (h, key, bullets, alt, cap) in enumerate(cards):
        x = L + i * (cw_ + g)
        s.box(x, yc, cw_, ch, fill=LIGHT)
        s.box(x, yc, cw_, 0.06, fill=BLUE)
        s.text(x + pad, yc + 0.17, cw_ - 2 * pad, 0.5, [
            Para(h, size=14, bold=True, color=NAVY),
            Para("POTENTIAL VALUE FOR LANDSLIDE ASSESSMENT", size=8.5, bold=True, color=PURPLE, track=0.5, sb=2),
        ])
        yi = yc + 0.76
        s.image(A[key], x + pad, yi, iw, ih, fit="cover", border=RULE, alt=alt)
        s.box(x + pad, yi, 1.72, 0.22, fill=NAVY, text=Para("EXAMPLE · NOT SSGI DATA", track=0.5),
              size=7.5, color=WHITE, bold=True, align="c", anchor="m", inset=0.0,
              name="Label: example image, not SSGI data")
        caption(s, x + pad, yi + ih + 0.06, cw_ - 2 * pad,
                f"Illustrative example, not SSGI data. {cap} Image: U.S. Geological Survey, public domain.",
                h=cap_h)
        tx = x + pad + iw + 0.2
        s.text(tx, yi - 0.02, x + cw_ - 0.14 - tx, ih + 0.04,
               [Para(b, bullet=True, sb=0 if j == 0 else 8) for j, b in enumerate(bullets)],
               size=11.5, color=INK, ls=1.0)
    s.box(L, yv, CW, BOTTOM - yv, fill=VERIFY_BG, text=(
        "**To be confirmed by SSGI:** LiDAR and aerial photography coverage areas · acquisition dates · "
        "point density · image scale or ground sampling distance · formats · access conditions. "
        "The images above are international examples, to be replaced with SSGI data."),
          size=10, color="#4A3A00", anchor="m", inset=(0.18, 0.04, 0.18, 0.04), ls=1.0,
          name="To be verified")
    s.notes("""
LiDAR AND AERIAL PHOTOGRAPHY (about 3 minutes)
- SSGI holds high-resolution LiDAR data and aerial photography. For landslide work these fill the gap between national satellite-scale mapping and what a field team sees on the ground.
- Read the workflow across the top: LiDAR and aerial imagery feed high-resolution terrain interpretation; that supports mapping of individual landslide features and a better inventory; field teams validate what is mapped; and the improved inventory in turn improves susceptibility and hazard assessment.
- LiDAR: produces detailed bare-earth terrain models. Derivatives such as slope, hillshade and surface roughness make scarps, deposits, tension cracks and old landslide bodies visible, including some under partial vegetation cover. This supports detailed terrain characterisation and slope-instability investigation at priority sites.
- Aerial photography: allows visual and stereo interpretation of landslide features. Where photographs from more than one date exist, they help identify and date past landslides, which matters because trigger development needs dated events. Photographs also give a detailed base for field mapping and for locating buildings and roads that are exposed.
- Both can be used to check satellite-based mapping and to refine susceptibility assessment at local scale in the hotspots that the national map identifies.

ABOUT THE TWO IMAGES: SAY THIS CLEARLY
- Both images are international examples from the U.S. Geological Survey. They are NOT SSGI data and NOT from Ethiopia. They are here only to show what each technique produces.
- Both show the same event: the landslide of 22 March 2014 near Oso, Washington State, USA.
- Left: a LiDAR terrain view coloured by elevation. The USGS notes that the LiDAR data show landslides of similar magnitude had occurred at the site hundreds or thousands of years ago. That is the point for this workshop: LiDAR can reveal old landslide bodies that are hard to see on the ground or in satellite imagery.
- Right: an oblique aerial photograph showing the whole landslide, from the source area to the end of its path. It is an oblique view, not a vertical survey photograph of the kind used for stereo mapping.

THIS SLIDE STILL NEEDS YOUR INPUT
- Replace the two example images with SSGI's own LiDAR and aerial imagery of an Ethiopian site when available.
- Do not state coverage, acquisition dates, point density, camera specifications, image scale or accuracy unless you have confirmed them. If they are not confirmed by the workshop, say so plainly and commit to providing them in the dataset catalogue (slide 16).

SOURCES: LiDAR image: U.S. Geological Survey, National Geospatial Program, "Oso Landslide Lidar", www.usgs.gov/media/images/oso-landslide-lidar (public domain). Aerial photograph: U.S. Geological Survey, "Oso, Washington Landslide", www.usgs.gov/media/images/oso-washington-landslide (public domain). The statement that LiDAR and aerial photography are available at SSGI comes from the presentation brief, not from the DMC slides.
""")


def slide_monitoring(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 4 — CONTINUOUS MONITORING OF RISK AREAS BY SATELLITE",
               "Satellite monitoring of risk areas: four options, each with clear limits",
               tags=("opportunity",))
    ratios = [1010 / 761, 1013 / 765, 1006 / 767, 641 / 538, 834 / 660]
    gap = 0.12
    h = (CW - 4 * gap) / sum(ratios)
    x = L
    for i, r in enumerate(ratios):
        s.image(A[f"insar{i + 1}"], x, t, h * r, h, border=RULE,
                alt=f"Interferogram {i + 1} of 5 over the Erta Ale and Hayli Gubbi volcanoes, October to November 2025")
        x += h * r + gap
    caption(s, L, t + h + 0.05, CW,
            "Repeat-pass interferograms, Erta Ale and Hayli Gubbi volcanoes, 1 October to 30 November 2025: "
            f"multi-temporal InSAR monitoring demonstrated by DMC on volcanic deformation. {SRC}, slide 6.", h=0.2)
    rows = [
        ["Monitoring option", "What it offers", "Limits to plan for"],
        ["**Sentinel-1 SAR / InSAR time series**",
         "Detects and tracks slow ground deformation on suitable slopes. Works through cloud and at night.",
         "Revisit of days, not hours. Loss of coherence under vegetation. Layover and shadow on steep "
         "slopes. Cannot follow sudden failure."],
        ["**Optical change detection** (Sentinel-2, Landsat)",
         "Maps new landslide scars and land-cover change. Keeps the inventory up to date.",
         "Cloud cover in the rainy season. Spatial resolution misses small slides. Detects events "
         "after they happen."],
        ["**Satellite rainfall and soil-moisture products**",
         "Area-wide indicators of wet conditions, to combine with ground rainfall data and forecasts.",
         "Coarse resolution. Must be checked against ground stations. Thresholds and forecasts are EMI-led."],
        ["**Integration with ground information**",
         "Field verification, geotechnical, soil-moisture and hydrological data confirm and explain "
         "satellite signals.",
         "Depends on partner data, field resources and agreed protocols."],
    ]
    yt = t + h + 0.36
    yb = s.table(L, yt, [2.75, 4.6, CW - 7.35], rows, size=10.8, color=INK, header_fill=PURPLE,
                 header_size=10.5, rule=RULE, pad=(0.09, 0.08), ls=0.98, name="Monitoring options")
    yn = max(yb + 0.14, BOTTOM - 0.5)
    s.box(L, yn, CW, BOTTOM - yn, fill=PURPLE_TINT, text=(
        "**These are technical options to be piloted with ground validation.** They are not evidence of an "
        "operational nationwide landslide early warning service."), size=10.5, color=PURPLE, anchor="m",
          inset=(0.18, 0.03, 0.18, 0.03))
    s.notes("""
SATELLITE MONITORING OPTIONS (about 4 minutes)
- The ToR asks us to discuss options for continuous monitoring of risk areas using satellite technology. We present four options and, for each, what it offers and what limits it.
- The image strip shows that the DMC has produced a sequence of repeat-pass interferograms: five dates over the Erta Ale and Hayli Gubbi volcanoes from 1 October to 30 November 2025, acquired at multiples of twelve days. That is a monitoring time series. It is on volcanic deformation, not landslides.

OPTION BY OPTION
1. Sentinel-1 InSAR time series. Useful for slow-moving slopes where the radar signal stays coherent. Each Sentinel-1 satellite repeats the same orbit every 12 days, so it sees gradual movement, not sudden collapse. Vegetation, steep terrain (layover and shadow) and slope orientation limit where it works. The DMC's comparison of Sentinel-1 with NISAR is relevant here because coherence is the main constraint.
2. Optical change detection with Sentinel-2 and Landsat. Good for mapping new scars after events and keeping the inventory current. Sentinel-2 offers 10 m and Landsat 30 m pixels, so small slides are missed, and cloud in the rainy season is the main obstacle. It reports what has happened; it does not warn.
3. Satellite rainfall and soil-moisture products. They give area-wide wetness indicators between gauges. They are coarse and must be calibrated against EMI ground observations. Rainfall thresholds and forecasts remain EMI's lead.
4. Integration with ground information. Satellite signals need confirmation from field verification and from geotechnical, soil-moisture and hydrological data held by partners.

POSITIONING
- These are monitoring opportunities and technical options. We are not claiming a fully operational nationwide landslide early warning service. The realistic next step is to test them at a small number of pilot sites.

SOURCES: DMC presentation slides 4, 5 and 6. Sentinel-1, Sentinel-2 and Landsat characteristics are public mission specifications.
""")


def slide_exposure(deck, A):
    s = deck.slide()
    t = chrome(s, A, "ToR 5.2 · CONTRIBUTION 5 — HAZARD MAPPING AND EXPOSURE ANALYSIS",
               "From susceptibility maps to exposure: who and what is at risk, and where to act first",
               tags=("existing", "opportunity"))
    steps = [
        ("Map susceptibility and hazard",
         "National, regional and local maps, using geospatial methods suited to each scale."),
        ("Identify and prioritise hotspots",
         "Rank areas by susceptibility class, together with partner knowledge of past events."),
        ("Overlay exposure layers",
         "Settlements, roads, bridges, public facilities, agricultural areas and other available datasets."),
        ("Quantify exposure",
         "Population and infrastructure in each hazard class, using authoritative, appropriately scaled data."),
        ("Prioritise action",
         "Locations for field verification, detailed investigation, monitoring and anticipatory action."),
    ]
    lw_ = 5.45
    g = 0.14
    sh = (BOTTOM - t - 4 * g) / 5
    y = t
    for i, (h, d) in enumerate(steps, 1):
        col = BLUE if i <= 3 else PURPLE
        s.box(L, y, lw_, sh, fill=BLUE_TINT if i <= 3 else PURPLE_TINT)
        s.box(L, y, 0.07, sh, fill=col)
        badge(s, L + 0.22, y + (sh - 0.4) / 2, i, d=0.4, fill=col)
        s.text(L + 0.78, y, lw_ - 0.9, sh, [
            Para(h, size=11.5, bold=True, color=NAVY),
            Para(d, size=9.8, color=INK, sb=1.5),
        ], anchor="m", ls=0.98)
        if i < 5:
            s.line(L + 0.42, y + sh, L + 0.42, y + sh + g, color=SLATE, lw=1.25, arrow=True)
        y += sh + g
    x = L + lw_ + 0.4
    iw = R - x
    _, iy, _, ih = s.image(A["geoportal_hazard"], x, t, iw, 3.3, valign="t", border=RULE,
                           alt="Screenshot of the Disaster Management Geoportal hazard map showing a landslide "
                               "risk raster over Ethiopia with a panel of administrative information for a clicked location")
    caption(s, x, iy + ih + 0.05, iw,
            "Disaster Management Geoportal, Hazard Map: landslide risk raster in five classes, with "
            f"administrative information returned for any clicked location. {SRC}, slide 15.")
    yl = iy + ih + 0.5
    label(s, x, yl, iw, "ONE WORKFLOW, THREE SCALES", color=NAVY)
    scales = [
        ("NATIONAL", "Screen priority regions with the national model."),
        ("ZONE / WOREDA", "Rank areas within a hotspot, as for Gamo Zone."),
        ("SITE", "LiDAR, aerial photographs and field survey."),
    ]
    sw = (iw - 2 * 0.12) / 3
    for i, (h, d) in enumerate(scales):
        sx = x + i * (sw + 0.12)
        s.box(sx, yl + 0.28, sw, BOTTOM - yl - 0.28, fill=LIGHT, text=[
            Para(h, size=9, bold=True, color=BLUE, track=0.5),
            Para(d, size=9.5, color=INK, sb=2),
        ], inset=(0.12, 0.06, 0.1, 0.04), anchor="m", ls=0.98)
    s.notes("""
HAZARD MAPPING AND EXPOSURE ANALYSIS (about 3 minutes)
- The ToR asks SSGI to support hazard mapping and exposure analysis. This is a five-step workflow. Steps 1 to 3 use capacity SSGI has today (blue); steps 4 and 5 are opportunities that depend on agreeing data with partners (purple).
1. Map susceptibility and hazard at national, regional and local scales.
2. Identify and prioritise hotspots, combining the maps with what AAU, EDRMC and regional offices know about past events.
3. Overlay exposure layers: settlements, roads, bridges, public facilities, agricultural areas and other available datasets.
4. Quantify exposure: how many people and which infrastructure fall in each hazard class. This needs authoritative, appropriately scaled population and infrastructure data, and the sources should be agreed with EDRMC and other partners.
5. Prioritise locations for field verification, detailed investigation, monitoring and anticipatory action.

THE EVIDENCE
- The screenshot is from the Disaster Management Geoportal, which SSGI already operates. Its Hazard Map carries a landslide risk raster in five classes, and clicking a location returns administrative information such as region and zone. The step from a hazard map to administrative units already exists in a working system.
- One workflow, three scales: the national model screens regions; a zone-level assessment like Gamo ranks areas within a hotspot; LiDAR, aerial photographs and field survey work at site level.

CAUTION
- A national susceptibility layer is suitable for screening and prioritising. It should not be used alone to decide on action at a specific village or slope; that needs local assessment and validation.
- We have not named specific exposure datasets because the sources are still to be agreed in this workshop. EDRMC's role in vulnerability and exposure analysis is set out in ToR Section 5.5.

SOURCES: DMC presentation slide 15; ToR Sections 5.2 and 5.5.
""")


def slide_systems(deck, A):
    s = deck.slide()
    t = chrome(s, A, "EXISTING SYSTEM DEVELOPMENT INITIATIVES",
               "SSGI has already developed four disaster information systems",
               tags=("existing",))
    systems = [
        ("Disaster Management Geoportal", "disaster.ssgi.gov.et", "geoportal_events", [
            "Central repository of disaster data, maps and documents",
            "Interactive hazard and risk visualisation, including a landslide layer",
            "Near-real-time and historical information; automated reports and dashboards",
        ], "Screenshot of the Disaster Management Geoportal showing mapped disaster events and totals by type", 15),
        ("Disaster Monitoring Website", None, "website", [
            "Monitors multiple hazards, including landslides",
            "Integrates open national and international disaster information",
            "Compares and validates local and global data; maps and dashboards",
        ], "Screenshot of the Disaster Monitoring Center website listing its monitoring activities", 16),
        ("Earthquake and Volcano Monitoring System", None, "eqvol", [
            "Hazard-specific monitoring and visualisation",
            "Map room with volcano and earthquake listings",
            "Colour-coded alert levels for monitored sites",
        ], "Screenshot of the Earthquake and Volcano Monitoring System map room", 17),
        ("Fire Resource and Incident Management System (FRIMS)", None, "frims", [
            "Near-real-time incident reporting to headquarters",
            "Finds the nearest station, facility or response unit; route guidance",
            "Coordination between field teams and headquarters",
        ], "Screenshot of the FRIMS fire alert system showing fire stations and incidents on a map", 18),
    ]
    g = 0.2
    cw_ = (CW - g) / 2
    ym = BOTTOM - 0.5
    ch = (ym - 0.16 - t - g) / 2
    iw = 3.25
    s.box(L, ym, CW, BOTTOM - ym, fill=GOLD_TINT)
    s.box(L, ym, 0.07, BOTTOM - ym, fill=GOLD)
    s.text(L + 0.22, ym, CW - 0.4, BOTTOM - ym,
           "**Why this matters for the EAP.** The building blocks of a landslide information system already "
           "exist here: hazard layers, event records, dashboards, reporting and field coordination.",
           size=10.5, color=INK, anchor="m", ls=1.0)
    for i, (name, url, key, bullets, alt, src_slide) in enumerate(systems):
        x = L + (i % 2) * (cw_ + g)
        y = t + (i // 2) * (ch + g)
        s.box(x, y, cw_, ch, fill=LIGHT)
        s.box(x, y, 0.07, ch, fill=BLUE)
        _, iy, _, ih = s.image(A[key], x + 0.22, y + 0.16, iw, ch - 0.5, valign="t", border=RULE, alt=alt)
        caption(s, x + 0.22, iy + ih + 0.05, iw, f"Screenshot. {SRC}, slide {src_slide}.", h=0.2)
        tx = x + 0.22 + iw + 0.2
        tw = cw_ - (tx - x) - 0.12
        paras = [Para(name, size=12, bold=True, color=NAVY)]
        if url:
            paras.append(Para(url, size=9.5, color=BLUE, sb=1))
        paras += [Para(b, bullet=True, size=9.8, color=INK, sb=5 if j == 0 else 3)
                  for j, b in enumerate(bullets)]
        s.text(tx, y + 0.14, tw, ch - 0.2, paras, ls=0.98)
    s.notes(f"""
EXISTING SYSTEMS (about 3 minutes)
- System development is a practical institutional strength. These four systems are shown in the DMC presentation.
1. Disaster Management Geoportal (disaster.ssgi.gov.et). A central repository for disaster data, maps and documents, with interactive hazard and risk visualisation, near-real-time and historical information, and automated reporting and dashboards for coordination among stakeholders. The screenshot shows mapped disaster events with totals by type, including a landslide count. The Geoportal's Hazard Map (previous slide) already includes a landslide layer.
2. Disaster Monitoring Website. Monitors multiple hazard types, including landslides; integrates data from open national and international disaster information systems; lets users compare and validate local and global data; and supports near-real-time monitoring, analysis and reporting.
3. Earthquake and Volcano Monitoring System. Shows that SSGI can build a monitoring and visualisation system for a specific hazard: a GIS map room with volcano and earthquake listings and colour-coded alert levels.
4. FRIMS, the Fire Resource and Incident Management System. Near-real-time incident reporting to headquarters, identification of the nearest fire station, facility or response unit, route guidance to the incident, and coordination between field teams and headquarters.

WHY THIS MATTERS FOR THE EAP
- A landslide information system would not start from zero. The components it needs (a hazard layer, event records, dashboards, reporting, incident and field coordination) already exist in these systems. The next slide shows how they could be combined.

VERIFY BEFORE PRESENTING
- Current operational status and user base of each system. The DMC slides describe functions; they do not state who uses each system or since when.
- The event totals visible in the Geoportal screenshot (for example Landslide: 115) are as displayed at the time of the screenshot. Do not present them as a national statistic; EDRMC holds the authoritative impact records.

{SRC}, slides 15, 16, 17 and 18.
""")


def slide_proposed(deck, A):
    s = deck.slide()
    t = chrome(s, A, "SYSTEM DEVELOPMENT · A PROPOSED NEXT STEP",
               "Proposed concept: a Landslide Monitoring and EAP Information System",
               tags=("proposed",))
    cols = [
        ("DATA INPUTS", [
            ("SSGI: satellite imagery, DEM, LiDAR, aerial photographs", True),
            ("SSGI: landslide inventory of 478 locations, to be completed with partners", True),
            ("EMI: rainfall observations and forecasts", False),
            ("MoWE: soil, soil moisture, hydrology", False),
            ("EDRMC: impact and exposure data; field reports", False),
        ]),
        ("DATA MANAGEMENT", [
            ("National landslide inventory database", False),
            ("Dataset catalogue and metadata", False),
            ("Common standards and formats", False),
            ("Data-sharing and access arrangements", False),
        ]),
        ("ANALYSIS", [
            ("Susceptibility and hazard maps", True),
            ("Satellite deformation products (InSAR)", False),
            ("Optical change detection", False),
            ("Rainfall and environmental indicators", False),
            ("Exposure analysis", False),
        ]),
        ("PRODUCTS", [
            ("Monitoring dashboard and map layers", False),
            ("Automated reports and bulletins", False),
            ("Alert dissemination, on triggers agreed by mandated institutions", False),
            ("Exposure information for priority areas", False),
        ]),
        ("USERS", [
            ("ERCS: EAP development and activation", False),
            ("EDRMC: preparedness and response", False),
            ("EMI and MoWE: joint analysis", False),
            ("Regional and local DRM offices", False),
            ("Universities and researchers", False),
        ]),
    ]
    n = 5
    g = 0.32
    w = (CW - (n - 1) * g) / n
    hh = 0.4
    y_items = t + hh + 0.12
    y_base = 5.72
    ih = (y_base - 0.14 - y_items - 4 * 0.08) / 5
    for i, (head, items) in enumerate(cols):
        x = L + i * (w + g)
        s.box(x, t, w, hh, fill=NAVY, text=Para(head, track=0.6), size=10, color=WHITE, bold=True,
              align="c", anchor="m", inset=0.0)
        if i < n - 1:
            s.line(x + w + 0.05, t + hh / 2, x + w + g - 0.05, t + hh / 2, color=NAVY, lw=1.5, arrow=True)
        y = y_items
        for txt, exists in items:
            if exists:
                s.box(x, y, w, ih, fill=BLUE_TINT, line=BLUE, lw=1.0, text=txt, size=9.3, color=INK,
                      anchor="m", inset=(0.1, 0.03, 0.08, 0.03), ls=0.96)
            else:
                s.box(x, y, w, ih, fill=WHITE, line=CRIMSON, lw=1.0, dash=True, text=txt, size=9.3,
                      color=INK, anchor="m", inset=(0.1, 0.03, 0.08, 0.03), ls=0.96)
            y += ih + 0.08
    bw = (CW - 0.2) / 2
    bh = 0.58
    s.box(L, y_base, bw, bh, fill=BLUE_TINT, line=BLUE, lw=1.0, text=(
        "**Builds on what exists:** Disaster Management Geoportal · Disaster Monitoring Website · "
        "DMC analysis and research capacity"), size=9.8, color=INK, anchor="m",
          inset=(0.14, 0.03, 0.1, 0.03), ls=0.98)
    s.box(L + bw + 0.2, y_base, bw, bh, fill=WHITE, line=CRIMSON, lw=1.0, dash=True, text=(
        "**Requires agreement on:** data sharing · standards and metadata · thresholds and "
        "roles set by the mandated institutions"), size=9.8, color=INK, anchor="m",
          inset=(0.14, 0.03, 0.1, 0.03), ls=0.98)
    yk = y_base + bh + 0.1
    s.box(L, yk + 0.05, 0.3, 0.17, fill=BLUE_TINT, line=BLUE, lw=1.0)
    s.text(L + 0.4, yk, 2.6, 0.27, "Exists today at SSGI", size=9.5, color=INK, anchor="m")
    s.box(L + 2.6, yk + 0.05, 0.3, 0.17, fill=WHITE, line=CRIMSON, lw=1.0, dash=True)
    s.text(L + 3.0, yk, 3.2, 0.27, "Proposed: does not exist yet", size=9.5, color=INK, anchor="m")
    s.text(L + 6.3, yk, CW - 6.3, 0.27, "A concept for discussion, not an operational system.",
           size=10, color=CRIMSON, bold=True, align="r", anchor="m")
    s.notes("""
PROPOSED SYSTEM (about 3 minutes)
- Say this first: this slide is a PROPOSAL. Nothing with a dashed red outline exists yet. It is a concept for discussion with partners.
- The idea is to apply SSGI's existing system-development experience to landslides, by linking five layers.
1. Data inputs. SSGI's satellite imagery, DEM, LiDAR and aerial photographs; the landslide inventory, to be completed with partners; rainfall observations and forecasts from EMI; soil, soil moisture and hydrology from MoWE; impact and exposure data from EDRMC; and field reports.
2. Data management. A national landslide inventory database, a dataset catalogue with metadata, common standards and formats, and agreed data-sharing and access arrangements.
3. Analysis. Susceptibility and hazard maps (these exist as research outputs), satellite deformation products, optical change detection, rainfall and environmental indicators, and exposure analysis.
4. Products. A monitoring dashboard and map layers, automated reports and bulletins, alert dissemination, and exposure information for priority areas.
5. Users. ERCS for EAP development and activation, EDRMC for preparedness and response, EMI and MoWE for joint analysis, regional and local DRM offices, and universities and researchers.

TWO POINTS TO STRESS
- It builds on what exists: the Geoportal, the monitoring website and the DMC's analysis capacity. The solid blue boxes mark what SSGI already has.
- Alert dissemination would act only on triggers agreed by the mandated institutions. SSGI would host and disseminate information; it would not set rainfall thresholds or decide on EAP activation.

SOURCES: components listed in the presentation brief; existing systems from DMC presentation slides 15 to 18. No part of the proposed system is described in the source documents as existing.
""")


def slide_collab(deck, A):
    s = deck.slide()
    t = chrome(s, A, "INSTITUTIONAL COLLABORATION AND DATA INTEGRATION",
               "SSGI’s geospatial contribution complements partner mandates; it does not replace them")
    s.box(L, t, CW, 0.62, fill=NAVY, text=(
        "**SSGI**   geospatial data  ·  Earth observation  ·  spatial analysis  ·  monitoring "
        "technologies  ·  disaster information systems"), size=12, color=WHITE, align="c", anchor="m")
    partners = [
        ("EMI", "Ethiopian Meteorological Institute",
         ["Historical and forecast rainfall", "Rainfall thresholds and their link to landslides",
          "Forecast lead times and confidence"],
         "Rainfall layers combined with susceptibility maps show where a forecast matters most."),
        ("MoWE", "Ministry of Water and Energy",
         ["Soil characteristics and soil moisture", "Hydrology, groundwater and watershed conditions",
          "Indicators of slope instability"],
         "Better soil and hydrological layers improve conditioning-factor inputs."),
        ("EDRMC", "Disaster Risk Management Commission",
         ["Historical records and impact data", "Affected populations, damages, lessons",
          "Vulnerability and exposure analysis"],
         "Impact records complete the inventory and validate hazard maps; exposure overlays."),
        ("ERCS", "Ethiopian Red Cross Society",
         ["Anticipatory Action framework", "Leads development of the Landslide EAP",
          "Coordinates follow-up actions"],
         "Maps and information products shaped to the EAP’s trigger and action needs."),
        ("AAU and partners", "Universities, research and technical partners",
         ["Studies of landslide occurrence", "Geological and geophysical drivers",
          "Known hotspots; research needs"],
         "Validation, joint research and review of methods."),
    ]
    g = 0.18
    w = (CW - 4 * g) / 5
    yc = t + 0.62 + 0.28
    ym = BOTTOM - 0.56
    ch = ym - 0.16 - yc
    for i, (abbr, full, brings, link) in enumerate(partners):
        x = L + i * (w + g)
        s.line(x + w / 2, t + 0.62, x + w / 2, yc, color=SLATE, lw=1.25)
        s.box(x, yc, w, ch, fill=LIGHT)
        s.box(x, yc, w, 0.66, fill=BLUE_TINT, text=[
            Para(abbr, size=13, bold=True, color=NAVY),
            Para(full, size=8.6, color=GREY, sb=1),
        ], inset=(0.14, 0.03, 0.08, 0.03), anchor="m", ls=0.96)
        s.text(x + 0.14, yc + 0.76, w - 0.26, 1.35, [
            Para("BRINGS (per ToR)", size=8.2, bold=True, color=GREY, track=0.5),
        ] + [Para(b, bullet=True, size=10, color=INK, sb=3) for b in brings], ls=0.98)
        yl = yc + ch - 0.98
        s.line(x + 0.14, yl, x + w - 0.14, yl, color=RULE, lw=0.75)
        s.text(x + 0.14, yl + 0.08, w - 0.26, 0.86, [
            Para("WITH SSGI", size=8.2, bold=True, color=BLUE, track=0.5),
            Para(link, size=10, color=BLUE, sb=2),
        ], ls=0.98)
    s.box(L, ym, CW, BOTTOM - ym, fill=GOLD_TINT)
    s.box(L, ym, 0.07, BOTTOM - ym, fill=GOLD)
    s.text(L + 0.22, ym, CW - 0.4, BOTTOM - ym,
           "**A trigger needs all of them:** where landslides are likely (SSGI), when rain is forecast (EMI), "
           "ground conditions (MoWE), who is exposed (EDRMC), what action follows (ERCS).",
           size=10.5, color=INK, anchor="m", ls=1.0)
    s.notes("""
COLLABORATION (about 2 minutes)
- No single institution can build the evidence base for a Landslide EAP. SSGI's geospatial contribution complements the mandates of partner institutions. It does not replace them.
- EMI: historical and forecast rainfall, rainfall thresholds, and advice on lead times and confidence. Combined with SSGI's susceptibility maps, a rainfall forecast can be directed to the places where it matters most.
- MoWE: soil characteristics, soil moisture, hydrology, groundwater and watershed conditions. Better soil and hydrological layers would directly improve the conditioning-factor inputs to the models.
- EDRMC: historical records, impact data, affected populations and vulnerability and exposure analysis. EDRMC's records can complete the landslide inventory and help validate the hazard maps.
- ERCS: presents the Anticipatory Action framework, leads development of the EAP and coordinates follow-up. SSGI's products should be shaped by what the EAP needs for triggers and actions.
- Addis Ababa University (Department of Geophysics) and other research and technical partners: existing studies, geological and geophysical drivers, known hotspots, and recommendations for further studies. The ToR also lists MoA, UN agencies, IFRC, NLRC and the Finnish Red Cross among participants.

MESSAGE
- Susceptibility from SSGI, rainfall from EMI, ground conditions from MoWE, impacts and exposure from EDRMC, and coordination by ERCS: together these make a trigger possible. Separately they do not.

SOURCES: ToR Sections 5.1 to 5.6.
""")


def slide_gaps(deck, A):
    s = deck.slide()
    t = chrome(s, A, "DATA GAPS, TECHNICAL CHALLENGES AND REQUIREMENTS",
               "Six points to confirm before geospatial products can support EAP triggers",
               tags=("verify",))
    items = [
        ("Data access and metadata",
         "Holdings, coverage and specifications of imagery, LiDAR and aerial photography.",
         "A dataset catalogue with metadata."),
        ("Inventory completeness",
         "Source, dates, landslide types and spatial spread of the 478 locations.",
         "One dated national inventory, reconciled with EDRMC and university records."),
        ("Field validation",
         "Independent accuracy of the susceptibility maps; metrics are not in the source slides.",
         "Validation sites and field campaigns."),
        ("Monitoring continuity",
         "Where InSAR works on vegetated and steep slopes; processing capacity for routine use.",
         "Pilot sites and a sustained processing routine."),
        ("Data standards",
         "Formats, coordinate systems, boundary versions and event-recording templates across institutions.",
         "Agreed common standards."),
        ("Inter-institutional sharing",
         "What can be shared, with whom, in what form and how often.",
         "A data-sharing protocol agreed through the technical working group."),
    ]
    g = 0.2
    w = (CW - 2 * g) / 3
    ys = 6.08
    ch = (ys - 0.16 - t - g) / 2
    for i, (h, confirm, need) in enumerate(items):
        x = L + (i % 3) * (w + g)
        y = t + (i // 3) * (ch + g)
        s.box(x, y, w, ch, fill=LIGHT)
        s.box(x, y, w, 0.06, fill=GOLD)
        badge(s, x + 0.16, y + 0.2, i + 1, d=0.36, fill=NAVY)
        s.text(x + 0.64, y + 0.2, w - 0.8, 0.36, h, size=12.5, bold=True, color=NAVY, anchor="m")
        s.text(x + 0.16, y + 0.7, w - 0.32, ch - 0.76, [
            Para("TO CONFIRM", size=8.2, bold=True, color=VERIFY_TX, track=0.5),
            Para(confirm, size=10.8, color=INK, sb=1.5),
            Para("REQUIREMENT", size=8.2, bold=True, color=BLUE, track=0.5, sb=7),
            Para(need, size=10.8, color=INK, sb=1.5),
        ], ls=0.98)
    s.box(L, ys, CW, BOTTOM - ys, fill=GOLD_TINT)
    s.box(L, ys, 0.07, BOTTOM - ys, fill=GOLD)
    s.text(L + 0.22, ys, CW - 0.4, BOTTOM - ys,
           "**Why dates matter.** A susceptibility map says where. Linking rainfall to landslide occurrence "
           "for trigger development (ToR objective 3) also needs to know when each past landslide happened.",
           size=10.5, color=INK, anchor="m", ls=1.0)
    s.notes("""
GAPS AND REQUIREMENTS (about 3 minutes)
- We present these as points to confirm, not as established gaps. Some may turn out to be well covered once institutions compare what they hold. That comparison is one purpose of this workshop.
1. Data access and metadata. We need to confirm what SSGI and partners hold and with what coverage and specifications, especially for LiDAR and aerial photography. Requirement: a dataset catalogue with metadata.
2. Inventory completeness. The model was trained on 478 landslide locations. We need to confirm their source, dates, landslide types and how evenly they cover the country. Requirement: one dated national inventory, reconciled with EDRMC and university records.
3. Field validation. The source slides do not report independent accuracy for the susceptibility maps. Requirement: validation sites and field campaigns, and reporting of standard performance measures.
4. Monitoring continuity. We need to find out where InSAR works on Ethiopia's vegetated and steep slopes, and what processing capacity routine monitoring would need. Requirement: pilot sites and a sustained processing routine.
5. Data standards. Institutions use different formats, coordinate systems, versions of administrative boundaries and ways of recording events. Requirement: agreed common standards.
6. Inter-institutional sharing. What can be shared, with whom, in what form and how often. Requirement: a data-sharing protocol agreed through the technical working group.

THE KEY TECHNICAL POINT
- Susceptibility mapping needs only the location of past landslides. Trigger development needs their dates as well, so that each event can be matched with the rainfall that preceded it. The ToR's third objective is to assess the relationship between rainfall patterns and landslide occurrence. A dated inventory is therefore the single most useful dataset the institutions could build together.

SOURCES: ToR Section 3 (objectives 1, 3 and 7) and expected outputs; observations on the DMC presentation.
""")


def slide_next(deck, A):
    s = deck.slide()
    t = chrome(s, A, "RECOMMENDATIONS AND WAY FORWARD",
               "Six practical next steps, proposed for the Day 2 roadmap discussion",
               tags=("proposed",))
    rows = [
        ["", "Recommended action", "Suggested lead", "With"],
        ["**1**", "**Agree priority hotspot areas**, using the national susceptibility map together with "
                  "partner knowledge of past events.",
         "Technical working group", "SSGI provides maps; AAU, EDRMC, regional offices"],
        ["**2**", "**Prepare a geospatial dataset catalogue with metadata**, including LiDAR and aerial "
                  "photography coverage.",
         "SSGI", "All data-holding institutions for their own datasets"],
        ["**3**", "**Share the available landslide inventory and map products** for review and completion.",
         "SSGI", "EDRMC, AAU"],
        ["**4**", "**Identify pilot monitoring sites** to test InSAR, optical change detection and field "
                  "validation together.",
         "SSGI", "AAU, EMI, MoWE, regional offices"],
        ["**5**", "**Define data-sharing protocols** and common data standards.",
         "Technical working group", "All institutions"],
        ["**6**", "**Establish the technical working group and a workplan** with roles and timeline.",
         "ERCS", "All institutions"],
    ]
    yb = s.table(L, t, [0.5, 6.35, 2.2, CW - 9.05], rows, size=12, color=INK, header_fill=NAVY,
                 header_size=10.5, rule=RULE, pad=(0.11, 0.13), ls=1.0, name="Recommended next steps")
    ybox = yb + 0.22
    s.box(L, ybox, CW, min(0.56, BOTTOM - ybox), fill=WHITE, line=CRIMSON, lw=1.0, dash=True, text=(
        "**For discussion.** Leads are suggestions that follow the roles in the ToR. Responsibilities and "
        "timelines are to be agreed by participants in the Day 2 roadmap session."),
          size=10.5, color=INK, anchor="m", inset=(0.18, 0.04, 0.18, 0.04), ls=1.0)
    s.notes("""
WAY FORWARD (about 3 minutes)
- Six practical steps. All are proposals for the Day 2 session on the roadmap and on roles and responsibilities. The suggested leads follow the roles set out in the ToR; they are for participants to confirm.
1. Agree priority hotspot areas. Use the national susceptibility map as a starting point and combine it with what AAU, EDRMC and regional offices know about past events.
2. Prepare a geospatial dataset catalogue with metadata. SSGI can lead for its own holdings, including confirming LiDAR and aerial photography coverage. Other institutions would describe their own datasets in the same format.
3. Share the available landslide inventory and map products so that EDRMC and AAU can review and complete them.
4. Identify a small number of pilot monitoring sites where InSAR, optical change detection and field validation are tested together. This turns the options on slide 10 into evidence.
5. Define data-sharing protocols and common standards through the working group.
6. Establish the technical working group and a workplan with roles and a timeline. The ToR assigns coordination of follow-up actions to ERCS.

WHAT SSGI CAN COMMIT TO
- Steps 2, 3 and 4 are within SSGI's own capacity to lead. State only the commitments that SSGI management has approved. No dates are shown on the slide because none have been agreed.

SOURCES: ToR Section 3 (objectives 6 and 8), expected outputs, Section 5.6 and the Day 2 programme; recommendations listed in the presentation brief.
""")


def slide_conclusion(deck, A):
    s = deck.slide()
    pw = 5.45
    s.box(0, 0, pw, deckkit.SLIDE_H, fill=NAVY, name="Conclusion panel")
    s.box(pw, 0, 0.07, deckkit.SLIDE_H, fill=GOLD)
    s.text(0.7, 0.72, pw - 1.2, 0.26, Para("CONCLUSION", track=0.7), size=9.5, color=GOLD, bold=True, anchor="m")
    s.text(0.7, 1.2, pw - 1.3, 3.1,
           "SSGI can support Ethiopia’s Landslide Early Action Protocol with data, Earth observation, "
           "analysis and information systems that exist today, and with realistic next steps.",
           size=20, color=WHITE, bold=True, ls=1.02)
    s.box(0.7, 4.55, 0.9, 0.05, fill=GOLD)
    s.text(0.7, 4.78, pw - 1.3, 1.2, [
        Para("Thank you", size=20, bold=True, color=WHITE),
        Para("www.ssgi.gov.et   ·   disaster.ssgi.gov.et", size=11, color="#C9D6DE", sb=8),
    ])
    s.text(0.7, 6.3, pw - 1.3, 0.52, [
        Para("PRESENTED BY", size=8.5, bold=True, color=GOLD, track=0.6),
        Para("Taye Teshome", size=14, bold=True, color=WHITE, sb=3),
    ], name="Presenter")
    x = pw + 0.55
    w = R - x
    s.title(x, 0.62, w, 0.45, "What SSGI brings to the Landslide EAP", size=24, color=NAVY)
    rows = [
        ("existing", BLUE, "Geospatial datasets",
         "Satellite imagery, DEM and terrain layers, and twelve national factor layers already assembled."),
        ("existing", BLUE, "LiDAR and aerial photography",
         "High-resolution terrain detail for inventories and site assessment. Coverage to be confirmed."),
        ("existing", BLUE, "Remote sensing and Earth observation expertise",
         "Optical, SAR and InSAR processing, demonstrated in DMC monitoring work."),
        ("research", GREEN, "Susceptibility mapping and machine learning",
         "The Gamo Zone assessment and a national model trained on 478 landslide locations."),
        ("opportunity", PURPLE, "Satellite monitoring options",
         "InSAR and optical change detection, to be piloted with ground validation."),
        ("existing", BLUE, "Disaster Monitoring Center and information systems",
         "Geoportal, monitoring website and hazard-specific systems: a base for a proposed landslide system."),
    ]
    y = 1.34
    rh = (5.98 - y) / 6
    for i, (kind, col, h, d) in enumerate(rows):
        if i:
            s.line(x, y, R, y, color=RULE, lw=0.75)
        s.box(x, y + 0.13, 0.07, rh - 0.26, fill=col)
        s.text(x + 0.24, y, w - 0.24, rh, [
            Para(h, size=12, bold=True, color=NAVY),
            Para(d, size=10, color=INK, sb=1.5),
        ], anchor="m", ls=1.0)
        y += rh
    s.line(x, y, R, y, color=RULE, lw=0.75)
    s.text(x, 6.12, w - 2.2, 0.7,
           "SSGI contributes the **where** and the **what is changing**. Together with EMI, MoWE, EDRMC, "
           "ERCS and research partners, that becomes a basis for **when** and **what to do**.",
           size=10.5, color=INK, anchor="m", ls=1.02)
    s.image(A["logo"], R - 1.95, 6.2, 1.95, 0.58, halign="r", alt="SSGI logo")
    s.notes("""
CONCLUSION (about 1 minute)
- Return to the question from the start: what can SSGI contribute, with its existing capacity and realistic next steps?
- Six things:
1. Geospatial datasets, including twelve national factor layers already assembled.
2. LiDAR and aerial photography for high-resolution assessment, with coverage to be confirmed in the dataset catalogue.
3. Remote sensing and Earth observation expertise in optical, SAR and InSAR processing.
4. Landslide susceptibility mapping and machine learning: the Gamo Zone assessment and a national model.
5. Satellite monitoring options, to be piloted and validated on the ground.
6. The Disaster Monitoring Center and working disaster information systems, which give a proposed landslide system a base to build on.
- SSGI contributes the "where" and the "what is changing". With EMI, MoWE, EDRMC, ERCS and research partners, that becomes a basis for "when" and "what to do".
- Thank the participants and invite questions. The next session is the discussion on remote sensing data, mapping capacity and monitoring opportunities.

BEFORE PRESENTING
- No personal e-mail or telephone number is shown. Add one under the name if you want participants to contact you directly.

SOURCES: summary of earlier slides; web addresses from DMC presentation slides 1 and 15.
""")


# ---------------------------------------------------------------- main
def build(dmc_pdf: Path, out: Path, preview_dir: Path | None, assets_dir: Path) -> Deck:
    assets = prepare_assets(dmc_pdf, assets_dir)
    deck = Deck(STYLES, preview=True)
    deck.set_theme("Arial", {"dk2": NAVY, "lt2": LIGHT, "accent1": BLUE, "accent2": GOLD, "accent3": GREEN,
                             "accent4": PURPLE, "accent5": CRIMSON, "accent6": SLATE})
    cp = deck.prs.core_properties
    cp.title = ("SSGI's Geospatial Capabilities and Contributions to the Development of a "
                "Landslide Early Action Protocol in Ethiopia")
    cp.author = "Space Science and Geospatial Institute (SSGI)"
    cp.subject = "Technical Consultation Workshop on the Development of a Landslide Early Action Protocol"
    cp.keywords = "landslide; early action protocol; SSGI; Disaster Monitoring Center; susceptibility"
    for fn in (slide_title, slide_context, slide_mandate, slide_dmc, slide_datasets, slide_rs,
               slide_gamo, slide_ml, slide_lidar, slide_monitoring, slide_exposure, slide_systems,
               slide_proposed, slide_collab, slide_gaps, slide_next, slide_conclusion):
        fn(deck, assets)
    out.parent.mkdir(parents=True, exist_ok=True)
    deck.save(out)
    if preview_dir:
        deck.save_previews(preview_dir)
    return deck


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dmc", required=True, type=Path, help="path to the DMC presentation PDF")
    ap.add_argument("--out", type=Path, default=HERE / "SSGI_Landslide_EAP_Workshop_Presentation.pptx")
    ap.add_argument("--preview", type=Path, default=None, help="write PNG previews of each slide here")
    ap.add_argument("--assets", type=Path, default=HERE / ".cache" / "assets",
                    help="where extracted figures are written")
    args = ap.parse_args()
    if not args.dmc.exists():
        sys.exit(f"DMC presentation not found: {args.dmc}")
    deck = build(args.dmc, args.out, args.preview, args.assets)
    print(f"wrote {args.out}  ({len(deck.slides)} slides, {args.out.stat().st_size / 1e6:.1f} MB)")
    if not deckkit.METRIC_FONTS_FOUND:
        print("note: Arial/Liberation Sans not found; layout checks used a fallback font and are approximate")
    if deck.warnings:
        print(f"{len(deck.warnings)} layout warning(s):")
        for w in deck.warnings:
            print("  -", w)
        sys.exit(2)
    print("layout check: no text overflow")


if __name__ == "__main__":
    main()
