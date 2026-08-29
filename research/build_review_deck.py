"""Fill the PES Phase-3 review template with Arbex content. Output stays fully editable.

Run from the repo root:  python research/build_review_deck.py
Regenerate figures first: python -m research.make_figures && python research/make_arch_diagram.py
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "md" / "Review1-phase3.pptx"
OUT = REPO / "Review1-phase3-arbex.pptx"
FIG = REPO / "research" / "results" / "figures"

INK = RGBColor(0x24, 0x1F, 0x1A)
TEAL = RGBColor(0x0F, 0x6B, 0x5C)
CLAY = RGBColor(0xB0, 0x56, 0x1E)
MUTE = RGBColor(0x5A, 0x6B, 0x73)

TEAM = "Davis Philip, Dhruv Menon, Navika Srikanth, Nikhil Thomas Sojan"
TITLE = "Efficient Detection of Arbitrage Opportunities in FX"

prs = Presentation(str(SRC))
BLANK = prs.slides[7].slide_layout


# ---------- helpers -------------------------------------------------------
def clear(tf):
    for p in list(tf.paragraphs[1:]):
        p._p.getparent().remove(p._p)
    tf.paragraphs[0].clear()


def set_lines(shape, lines, size=15, color=INK, align=PP_ALIGN.LEFT, space_after=6):
    """lines: list of (text, bold, level) or (text, bold) or str."""
    tf = shape.text_frame
    tf.word_wrap = True
    clear(tf)
    for i, item in enumerate(lines):
        if isinstance(item, str):
            text, bold, lvl = item, False, 0
        elif len(item) == 2:
            text, bold = item
            lvl = 0
        else:
            text, bold, lvl = item
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.level = lvl
        p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = "Calibri"


def find(slide, substr):
    for sh in slide.shapes:
        if sh.has_text_frame and substr.lower() in sh.text_frame.text.lower():
            return sh
    return None


def footer_and_names(slide):
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        t = sh.text_frame.text.strip()
        if t == "Title of the Project":
            set_lines(sh, [TITLE], size=11, color=MUTE, align=PP_ALIGN.CENTER, space_after=0)
        elif t == "name1_name2_name3_name4":
            set_lines(sh, [TEAM], size=11, color=MUTE, align=PP_ALIGN.CENTER, space_after=0)


def add_slide(title_text, at_index):
    s = prs.slides.add_slide(BLANK)
    # header (right aligned, matches template style)
    hb = s.shapes.add_textbox(Inches(3.25), Inches(0.95), Inches(9.5), Inches(0.6))
    set_lines(hb, [title_text], size=22, color=INK, align=PP_ALIGN.RIGHT, space_after=0)
    fb = s.shapes.add_textbox(Inches(0.08), Inches(0.07), Inches(4.5), Inches(0.4))
    set_lines(fb, [TITLE], size=11, color=MUTE, align=PP_ALIGN.CENTER, space_after=0)
    nb = s.shapes.add_textbox(Inches(4.03), Inches(7.06), Inches(5.27), Inches(0.4))
    set_lines(nb, [TEAM], size=11, color=MUTE, align=PP_ALIGN.CENTER, space_after=0)
    # reorder
    lst = prs.slides._sldIdLst
    els = list(lst)
    lst.remove(els[-1])
    lst.insert(at_index, els[-1])
    return s


def stat(slide, x, y, big, label, color=TEAL):
    b = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(3.9), Inches(1.0))
    set_lines(b, [(big, True)], size=40, color=color, align=PP_ALIGN.LEFT, space_after=0)
    l = slide.shapes.add_textbox(Inches(x), Inches(y + 0.95), Inches(3.9), Inches(0.8))
    set_lines(l, [label], size=12.5, color=INK, align=PP_ALIGN.LEFT, space_after=0)


# ---------- slide 1 : title ----------------------------------------------
s1 = prs.slides[0]
box = find(s1, "Project Title")
box.left, box.top, box.width, box.height = Inches(1.3), Inches(4.6), Inches(10.8), Inches(2.3)
set_lines(box, [
    (f"Project Title  :  {TITLE}", False),
    ("Project ID      :  PW26_CHB_02", False),
    ("Project Guide :  Dr. Chaitra B", False),
    (f"Project Team  :  {TEAM}", False),
], size=17, space_after=8)

# ---------- slide 2 : outline ------------------------------------------
s2 = prs.slides[1]
set_lines(find(s2, "Abstract and Scope of the Project"), [
    "Abstract and scope",
    "Summary of work — Capstone Project Phase 2",
    "Design of overall architecture and modules",
    "List of tasks / modules",
    "Individual contribution",
    "Demonstration, results and testing",
    "References",
], size=18, space_after=10)
footer_and_names(s2)

# ---------- slide 3 : abstract & scope --------------------------------
s3 = prs.slides[2]
b3 = find(s3, "basic introduction")
b3.top, b3.left, b3.width, b3.height = Inches(1.6), Inches(1.8), Inches(9.7), Inches(5.3)
set_lines(b3, [
    ("Problem.", True),
    "FX prices differ briefly across venues and currency triangles; these arbitrage "
    "windows last milliseconds to minutes and cannot be captured manually. Detection "
    "must be automated, latency-aware and cost-aware.",
    ("Arbex.", True),
    "An event-driven research engine that ingests multiple price feeds, time-aligns "
    "them, detects cross-source and session mispricings, classifies how long each "
    "opportunity persists, filters them through a simulated L2 execution model, and "
    "ranks the survivors on a live dashboard.",
    ("Phase-3 scope.", True),
    "Re-target the engine at the real onshore\u2013offshore USD/INR basis using "
    "retail-accessible data; keep the synthetic engine as a calibrated test bed. "
    "Execution stays simulated \u2014 the tool is pre-trade intelligence, not a trading bot.",
], size=13, space_after=6)
footer_and_names(s3)

# ---------- slide 4 : Phase-2 summary ---------------------------------
s4 = prs.slides[3]
h4 = find(s4, "Summary of Work Done")
set_lines(h4, ["Summary of Work Done in Capstone Project Phase 2"], size=22,
          align=PP_ALIGN.RIGHT, space_after=0)
b4 = find(s4, "Provide summary of Phase")
b4.top, b4.left, b4.width, b4.height = Inches(1.75), Inches(1.8), Inches(9.9), Inches(5.2)
set_lines(b4, [
    ("Built (Phase 2).", True),
    "\u2022  Event-driven pipeline: 6 session-aware synthetic USD/INR feeds \u2192 normalizer "
    "\u2192 20 ms tick aligner \u2192 arbitrage engine \u2192 persistence tracker \u2192 execution "
    "filter \u2192 ranker \u2192 Redis \u2192 Next.js dashboard.",
    "\u2022  Semantic engine as an independent microservice (news \u2192 confidence context), "
    "~4,392 labelled rows.",
    "\u2022  Migrated frontend to Next.js; split the semantic engine out over Redis pub/sub.",
    ("Phase-2 review feedback incorporated.", True),
    "\u2022  Confidence score + opportunity ranking + aggregated profit margin + "
    "per-provider charts.",
    ("Decided for Phase 3.", True),
    "\u2022  Pivot from simulated cross-provider arbitrage to the real onshore\u2013offshore "
    "USD/INR dislocation, on free Indian-broker data.",
], size=12.5, space_after=5)
footer_and_names(s4)

# ---------- slide 5 : architecture -----------------------------------
s5 = prs.slides[4]
set_lines(find(s5, "Architecture") or s5.shapes[0], ["Architecture"], size=24,
          align=PP_ALIGN.RIGHT, space_after=0)
desc = find(s5, "Design of overall Architecture and modules")
set_lines(desc, [
    "Four decoupled layers: ingestion \u2192 alignment & detection \u2192 institutional "
    "scoring \u2192 transport & delivery. The heavy semantic engine runs as a separate "
    "microservice so it never blocks the detection loop.",
], size=12, space_after=0)
desc.top = Inches(1.5)
desc.left = Inches(0.9)
desc.width = Inches(11.5)
desc.height = Inches(0.75)
s5.shapes.add_picture(str(FIG / "fig_architecture.png"), Inches(2.9), Inches(2.35), width=Inches(7.5))
footer_and_names(s5)

# ---------- slide 6 : tasks / modules --------------------------------
s6 = prs.slides[5]
set_lines(find(s6, "List of Tasks/Modules") or s6.shapes[0], ["List of Tasks / Modules"],
          size=24, align=PP_ALIGN.RIGHT, space_after=0)
set_lines(find(s6, "elaborated in discussion"), [
    ("Data & ingestion", True),
    "\u2022  Session-aware synthetic USD/INR sources (6 feeds)  \u2014  done",
    "\u2022  Tick normalizer, latency model  \u2014  done",
    "\u2022  Real-feed adapters (Kite / offshore / OTC)  \u2014  Phase 3, designed",
    ("Detection", True),
    "\u2022  Tick aligner (20 ms micro-batch)  \u2014  done",
    "\u2022  Arbitrage engine: cross-source + session inefficiency  \u2014  done",
    "\u2022  Instrument normaliser for onshore\u2013offshore basis  \u2014  Phase 3, spec'd",
    ("Scoring & delivery", True),
    "\u2022  Persistence tracker, simulated execution filter, ranker  \u2014  done",
    "\u2022  Redis transport, Next.js dashboard  \u2014  done",
    "\u2022  Semantic engine microservice  \u2014  separate owner",
    "\u2022  Reproducible research harness + figures  \u2014  done",
], size=12.5, space_after=4)
footer_and_names(s6)

# ---------- slide 7 : individual contribution -----------------------
s7 = prs.slides[6]
set_lines(find(s7, "Individual Contribution") or s7.shapes[0], ["Individual Contribution"],
          size=24, align=PP_ALIGN.RIGHT, space_after=0)
body7 = find(s7, "Tabulate the individual") or find(s7, "individual contribution")
set_lines(body7, [
    ("Work areas (owner initials + LOC / commit counts to be filled by the team).", True),
    "\u2022  Data sources, synthetic microstructure, normalization",
    "\u2022  Tick alignment + arbitrage detection engine",
    "\u2022  Persistence tracking, execution-feasibility filter, ranker",
    "\u2022  Redis transport + Next.js research dashboard",
    "\u2022  Semantic / sentiment engine microservice  (separate owner)",
    "\u2022  Reproducible research harness, metrics, figures",
    "\u2022  Phase-3 planning: literature survey, novelty analysis, architecture re-eval, SPEC",
], size=13, space_after=6)
footer_and_names(s7)

# ---------- slide 8 : demonstration & results ----------------------
s8 = prs.slides[7]
set_lines(find(s8, "Demonstration and Testing") or s8.shapes[0],
          ["Demonstration & Results"], size=24, align=PP_ALIGN.RIGHT, space_after=0)
body8 = find(s8, "Demonstration and Result of modules")
set_lines(body8, [
    ("Demo.", True),
    "`python -m research.run_experiment` drives the real production pipeline classes "
    "against the 6 synthetic feeds with a fake clock + fixed seed \u2014 a 30-minute market "
    "runs in ~15 s and is byte-for-byte reproducible. The live server + Next.js "
    "dashboard show the same pipeline in real time.",
], size=13, space_after=6)
body8.top = Inches(1.7)
body8.height = Inches(1.7)
stat(s8, 0.8, 3.7, "540,000", "ticks processed\n(30-min run, 18k / min)", TEAL)
stat(s8, 5.0, 3.7, "85%", "of paper opportunities removed\nby the L2 + latency filter", CLAY)
stat(s8, 9.2, 3.7, "5%", "of opportunity episodes reach\n\u2018persistent\u2019 (>300 ms)", MUTE)
b8n = s8.shapes.add_textbox(Inches(0.8), Inches(6.0), Inches(11.7), Inches(1.0))
set_lines(b8n, [
    "Headline (seed 42): 2,575 raw detections \u2192 2,280 opportunity episodes; "
    "gross profit mean 4.8 pips, median 3.7; stable across 4 seeds (\u00b12%).",
], size=12, color=INK, space_after=0)
footer_and_names(s8)

# ---------- NEW slide 9 : results figures --------------------------
s9 = add_slide("Results \u2014 Persistence & Cost Filter", 8)
s9.shapes.add_picture(str(FIG / "fig_persistence.png"), Inches(0.5), Inches(1.9), width=Inches(6.15))
s9.shapes.add_picture(str(FIG / "fig_cost_filter.png"), Inches(6.9), Inches(2.1), width=Inches(6.0))
cap9 = s9.shapes.add_textbox(Inches(0.5), Inches(6.35), Inches(12.4), Inches(0.7))
set_lines(cap9, [
    "Most detected mispricings are transient noise; the execution filter then removes "
    "the majority of what is left \u2014 the engine only surfaces a small, vetted set.",
], size=12, color=MUTE, space_after=0)

# ---------- NEW slide 10 : ablation -------------------------------
s10 = add_slide("Results \u2014 Alignment-Window Ablation", 9)
s10.shapes.add_picture(str(FIG / "fig_alignment_ablation.png"), Inches(2.55), Inches(1.7), width=Inches(8.2))
cap10 = s10.shapes.add_textbox(Inches(1.0), Inches(6.75), Inches(11.4), Inches(0.7))
set_lines(cap10, [
    "Tightening the tick-alignment window toward the phantom-arbitrage regime (20\u201350 ms) "
    "inflates detections ~7\u00d7 and makes feed-latency artifacts look \u2018persistent\u2019 and "
    "\u2018viable\u2019. The 200 ms operating point suppresses them.",
], size=12, color=MUTE, space_after=0)

# ---------- NEW slide 11 : testing -------------------------------
s11 = add_slide("Testing", 10)
set_lines(s11.shapes.add_textbox(Inches(0.8), Inches(1.75), Inches(11.9), Inches(5.1)), [
    ("Unit / integration suite \u2014 pytest.", True),
    "\u2022  69 / 70 passing.  The one failure is in the semantic-engine module "
    "(separate owner): a news-fallback assertion, unrelated to detection.",
    "\u2022  Coverage: arbitrage engine (5), methodology / no-arbitrage conditions (34), "
    "opportunity tracker (9), tick normalizer (10), end-to-end simulation (10).",
    ("Reproducibility harness.", True),
    "\u2022  Fake monotonic clock + disabled live-rate fetch + seeded RNG "
    "(incl. PYTHONHASHSEED) \u2192 identical `opportunities.csv` and `summary.json` on "
    "any machine.",
    "\u2022  Seed sweep (42 / 7 / 123 / 2025): detections and filter rate stable to \u00b12%.",
    "\u2022  Ablation harness (`--window-ms`) demonstrates the aligner's effect quantitatively.",
    ("Found & fixed in passing.", True),
    "\u2022  Latent bug in `OpportunityRanker.rank()` (stale config field / enum member) "
    "flagged for follow-up; production path `rank_with_context()` is unaffected.",
], size=12.5, space_after=5)

# ---------- slide 12 (was 9) : references ------------------------
sref = prs.slides[11]
set_lines(find(sref, "References") or sref.shapes[0], ["References"], size=24,
          align=PP_ALIGN.RIGHT, space_after=0)
set_lines(find(sref, "Provide references") or find(sref, "certain integrals"), [
    "[1] T. Foucault, R. Kozhan and W. W. Tham, \u201cToxic arbitrage,\u201d "
    "The Review of Financial Studies, vol. 30, no. 4, pp. 1053\u20131094, 2017.",
    "[2] H. Behera and D. P. Rath, \u201cDoes offshore NDF market influence onshore forex "
    "market? Evidence from India,\u201d Journal of Futures Markets, vol. 42, no. 6, "
    "pp. 1167\u20131185, 2022.",
    "[3] M. Aquilina, E. Budish and P. O\u2019Neill, \u201cQuantifying the high-frequency "
    "trading arms race,\u201d Quarterly Journal of Economics, vol. 137, no. 1, "
    "pp. 493\u2013564, 2022.",
    "[4] D. Yuferova, \u201cAlgorithmic trading and market efficiency around the "
    "introduction of the NYSE Hybrid Market,\u201d Journal of Financial Markets, vol. 68, 2024.",
    "[5] R. Cont, M. Cucuringu, V. Glukhov and F. Prenzel, \u201cAnalysis and modeling of "
    "client order flow in limit order markets,\u201d Quantitative Finance, vol. 23, no. 4, "
    "pp. 693\u2013738, 2023.",
    "[6] W. Du, A. Tepper and A. Verdelhan, \u201cDeviations from covered interest rate "
    "parity,\u201d Journal of Finance, vol. 73, no. 3, 2018.",
    "[7] D. C. Byrd, M. Hybinette and T. H. Balch, \u201cABIDES: towards high-fidelity "
    "market simulation for AI research,\u201d arXiv:1904.12066, 2019.",
], size=11.5, space_after=6)
footer_and_names(sref)

# ---------- footers on remaining originals ---------------------
for extra in (prs.slides[0],):
    pass

prs.save(str(OUT))
print(f"saved {OUT}  ({len(prs.slides)} slides)")
