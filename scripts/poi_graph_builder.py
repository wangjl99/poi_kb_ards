#!/usr/bin/env python3
"""
poi_graph_builder.py  (v2 — BioLink aligned)
=============================================
Improvements over v1:
  - node_type column (BioLink category) in nodes table
  - subject_type + object_type columns in edges table
  - BioLink predicate mapping for all 26 POI properties
  - Confidence score documented and consistent
  - Robust string→float confidence conversion
  - KGX-compatible output format

BioLink alignment:
  POI_intervention → biolink:Treatment
  POI_disease      → biolink:Disease
  POI_outcome      → biolink:ClinicalFinding
  POI_mechanism    → biolink:BiologicalProcess
  POI_biomarker    → biolink:Biomarker
  POI_population   → biolink:PopulationOfIndividualOrganisms
  POI_context      → biolink:InformationContentEntity

Confidence score definition:
  A composite curation score (0–1) reflecting:
    - Trial design quality (RCT=high, observational=medium)
    - Statistical strength (p<0.01=+0.1, p<0.05=base, p>0.05=-0.15)
    - Sample size (>500=+0.05, <100=-0.10)
    - Early stopping bias (stopped early=-0.05 to -0.10)
    - Crossover contamination (>20% crossover=-0.15)
    - Replication status (replicated=+0.10)
  Values: 0.99=definitive, 0.85=strong, 0.65=moderate, 0.45=weak

Usage:
  python3 poi_graph_builder.py
  python3 poi_graph_builder.py --source gold
  python3 poi_graph_builder.py --min-confidence 0.65
  python3 poi_graph_builder.py --format ttl
"""

import argparse
import json
import re
import csv
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE     = Path(__file__).resolve().parent
MASTER   = BASE / "corpus_analysis" / "master_curated.jsonl"
OWL_FILE = BASE / "corpus_analysis" / "ontology_output" / "ards_poi_ontology_v10.owl"
GOLD_DIR = BASE / "gold_standard"
OUT_DIR  = BASE / "poi_graph"
OUT_DIR.mkdir(exist_ok=True)

# ── BioLink node type mapping ─────────────────────────────────────────────────
POI_TO_BIOLINK = {
    "POI_intervention": "biolink:Treatment",
    "POI_disease":      "biolink:Disease",
    "POI_outcome":      "biolink:ClinicalFinding",
    "POI_mechanism":    "biolink:BiologicalProcess",
    "POI_biomarker":    "biolink:Biomarker",
    "POI_population":   "biolink:PopulationOfIndividualOrganisms",
    "POI_context":      "biolink:InformationContentEntity",
    "POI_unknown":      "biolink:NamedThing",
}

# ── BioLink predicate mapping ─────────────────────────────────────────────────
POI_TO_BIOLINK_PRED = {
    "poi:treats":                    "biolink:treats",
    "poi:hasClinicalOutcome":        "biolink:has_phenotype",
    "poi:causesAdverseOutcome":      "biolink:causes_adverse_event",
    "poi:hasBiomarker":              "biolink:has_biomarker",
    "poi:isContraindicatedIn":       "biolink:contraindicated_in",
    "poi:hasDifferentialResponse":   "biolink:related_to",
    "poi:hasPathophysiology":        "biolink:related_to",
    "poi:hasRiskFactor":             "biolink:has_risk_factor",
    "poi:hasAetiology":              "biolink:has_risk_factor",
    "poi:preventsCondition":         "biolink:prevents",
    "poi:improvesPhysiologicParameter":"biolink:ameliorates_condition",
    "poi:worsensClinicalParameter":  "biolink:disrupts",
    "poi:progressesTo":              "biolink:precedes",
    "poi:temporallyPrecedes":        "biolink:precedes",
    "poi:occursInContext":           "biolink:occurs_in",
    "poi:requiresClinicalContext":   "biolink:occurs_in",
    "poi:hasTemporalContext":        "biolink:occurs_in",
    "poi:hasSeverityStage":          "biolink:related_to",
    "poi:hasSubphenotype":           "biolink:has_phenotype",
    "poi:measuredBy":                "biolink:related_to",
    "poi:isBiomarkerOf":             "biolink:biomarker_for",
    "poi:isTreatedBy":               "biolink:treated_by",
    "poi:activatesPathway":          "biolink:positively_regulates",
    "poi:inhibitsPathway":           "biolink:negatively_regulates",
    "poi:biologicallyInteractsWith": "biolink:interacts_with",
    "poi:affectsOrganSystem":        "biolink:affects",
}

# ── Direction → predicate + confidence ───────────────────────────────────────
DIRECTION_TO_PRED = {
    "BENEFICIAL": "poi:hasClinicalOutcome",
    "HARMFUL":    "poi:causesAdverseOutcome",
    "NULL":       "poi:hasClinicalOutcome",
    "TREND":      "poi:hasClinicalOutcome",
    "MIXED":      "poi:hasClinicalOutcome",
    "benefit":    "poi:hasClinicalOutcome",
    "harm":       "poi:causesAdverseOutcome",
    "null":       "poi:hasClinicalOutcome",
}

# Confidence baseline by direction
# (adjusted for trial-specific factors in gold edges)
DIRECTION_TO_CONF = {
    "BENEFICIAL": 0.85,
    "HARMFUL":    0.85,
    "NULL":       0.75,
    "TREND":      0.60,
    "MIXED":      0.55,
    "benefit":    0.85,
    "harm":       0.85,
    "null":       0.75,
}

# String confidence → float
CONF_MAP = {
    "HIGHEST":      0.99,
    "HIGH":         0.85,
    "MEDIUM-HIGH":  0.75,
    "MEDIUM":       0.65,
    "LOW-MEDIUM":   0.55,
    "LOW":          0.45,
    "LOWEST":       0.30,
}

def parse_confidence(raw):
    """Convert any confidence representation to float 0–1."""
    if raw is None:
        return 0.65
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).upper().strip()
    if s in CONF_MAP:
        return CONF_MAP[s]
    # Handle "confidence=0.85" format
    m = re.search(r"(\d+\.?\d*)", s)
    if m:
        v = float(m.group(1))
        return v if v <= 1.0 else v / 100.0
    return 0.65


# ── Subject/object type inference ─────────────────────────────────────────────
def infer_subset(label):
    """Heuristic subset assignment when not known from ontology."""
    l = label.lower()
    if any(w in l for w in ["mortality","survival","vfd","ventilator-free",
                              "stay","intubation","extubation","outcome","days"]):
        return "POI_outcome"
    if any(w in l for w in ["ards","pneumonia","sepsis","injury","failure",
                              "syndrome","disease","infection"]):
        return "POI_disease"
    if any(w in l for w in ["prone","ventilation","ecmo","oxygen","dexameth",
                              "cisatracu","peep","recruitment","strategy",
                              "therapy","treatment","management","blockade"]):
        return "POI_intervention"
    if any(w in l for w in ["volutrauma","atelectrauma","biotrauma","pathway",
                              "mechanism","activation","signaling","process"]):
        return "POI_mechanism"
    if any(w in l for w in ["il-","interleukin","srage","angiopoietin","pai-",
                              "protein c","tnf","biomarker","level","plasma"]):
        return "POI_biomarker"
    if any(w in l for w in ["patient","population","obese","immunocompromised",
                              "elderly","pediatric","pregnant","covid"]):
        return "POI_population"
    if any(w in l for w in ["context","era","setting","period","phase","stage"]):
        return "POI_context"
    return "POI_unknown"


# ── Node and Edge classes ─────────────────────────────────────────────────────

class Node:
    def __init__(self, label, subset="POI_unknown", iri="", pmid=""):
        self.label      = label.strip()
        self.subset     = subset if subset else "POI_unknown"
        self.node_type  = POI_TO_BIOLINK.get(self.subset, "biolink:NamedThing")
        self.iri        = iri or self._make_iri(label)
        self.pmid       = pmid
        self.node_id    = re.sub(r"[^a-z0-9]", "_",
                                  label.lower().strip())[:60].rstrip("_")

    def _make_iri(self, label):
        slug = re.sub(r"[^a-z0-9_]", "_", label.lower().strip())[:40]
        return f"http://poi-kb.org/ards/kg/{slug}"

    def to_dict(self):
        return {
            "node_id":   self.node_id,
            "label":     self.label,
            "subset":    self.subset,
            "node_type": self.node_type,
            "iri":       self.iri,
            "pmid":      self.pmid,
        }


class Edge:
    def __init__(self, subj_label, predicate, obj_label,
                 subj_subset="", obj_subset="",
                 pmid="", trial="", year=0,
                 confidence=0.65, effect_size="", p_value="",
                 direction="", temporal_context="",
                 confidence_rationale=""):
        self.subj_label    = subj_label.strip()
        self.predicate     = predicate
        self.biolink_pred  = POI_TO_BIOLINK_PRED.get(predicate, "biolink:related_to")
        self.obj_label     = obj_label.strip()
        self.subj_subset   = subj_subset or infer_subset(subj_label)
        self.obj_subset    = obj_subset  or infer_subset(obj_label)
        self.subj_type     = POI_TO_BIOLINK.get(self.subj_subset, "biolink:NamedThing")
        self.obj_type      = POI_TO_BIOLINK.get(self.obj_subset,  "biolink:NamedThing")
        self.pmid          = str(pmid)
        self.trial         = trial
        self.year          = int(year) if year else 0
        self.confidence    = parse_confidence(confidence)
        self.effect_size   = effect_size
        self.p_value       = str(p_value)
        self.direction     = direction
        self.temporal_context = temporal_context
        self.confidence_rationale = confidence_rationale
        self.edge_id       = (
            f"{re.sub(r'[^a-z0-9]','_',subj_label.lower()[:20])}"
            f"__{predicate.split(':')[-1]}"
            f"__{re.sub(r'[^a-z0-9]','_',obj_label.lower()[:20])}"
            f"__{str(pmid)[:8]}"
        )

    def to_dict(self):
        return {
            "edge_id":              self.edge_id,
            "subject":              self.subj_label,
            "subject_type":         self.subj_type,
            "subject_subset":       self.subj_subset,
            "predicate":            self.predicate,
            "biolink_predicate":    self.biolink_pred,
            "object":               self.obj_label,
            "object_type":          self.obj_type,
            "object_subset":        self.obj_subset,
            "pmid":                 self.pmid,
            "trial":                self.trial,
            "year":                 self.year,
            "confidence":           round(self.confidence, 3),
            "confidence_rationale": self.confidence_rationale,
            "effect_size":          self.effect_size,
            "p_value":              self.p_value,
            "direction":            self.direction,
            "temporal_context":     self.temporal_context,
        }


# ── Gold assertions ───────────────────────────────────────────────────────────
# Confidence rationale for each gold edge:
#   Format: (subj, subset_s, pred, obj, subset_o, pmid, trial, year, conf, effect, direction, rationale)

GOLD_EDGES = [
    ("Low tidal volume ventilation",    "POI_intervention",
     "poi:treats",
     "Acute respiratory distress syndrome", "POI_disease",
     "10793162","ARMA",2000, 0.99, "ARD -8.8%","BENEFICIAL",
     "Large RCT n=861, stopped early efficacy, P=0.007, replicated globally, 25yr standard of care"),

    ("Low tidal volume ventilation",    "POI_intervention",
     "poi:hasClinicalOutcome",
     "28-day all-cause mortality",          "POI_outcome",
     "10793162","ARMA",2000, 0.99, "31.0% vs 39.8%","BENEFICIAL",
     "Primary endpoint, P=0.007, effect confirmed in meta-analyses"),

    ("Prone positioning",               "POI_intervention",
     "poi:treats",
     "Severe ARDS context PaO2/FiO2 <150", "POI_disease",
     "23688302","PROSEVA",2013, 0.99, "ARD -16.8%","BENEFICIAL",
     "Large RCT n=466, P<0.001, HR 0.39, not stopped early, strongest mortality signal in ARDS"),

    ("Prone positioning",               "POI_intervention",
     "poi:hasClinicalOutcome",
     "28-day all-cause mortality",          "POI_outcome",
     "23688302","PROSEVA",2013, 0.99, "HR 0.39 (0.25-0.63)","BENEFICIAL",
     "Primary endpoint, P<0.001, prespecified, replicated in meta-analyses"),

    ("Prone positioning",               "POI_intervention",
     "poi:requiresClinicalContext",
     "Severe ARDS context PaO2/FiO2 <150", "POI_context",
     "23688302","PROSEVA",2013, 0.99, "","BENEFICIAL",
     "Context mandatory: benefit only shown in PaO2/FiO2 <150 with PEEP>=5"),

    ("Aggressive recruitment maneuver strategy","POI_intervention",
     "poi:causesAdverseOutcome",
     "28-day all-cause mortality",          "POI_outcome",
     "28973363","ART",2017, 0.92, "HR 1.20 (1.01-1.42)","HARMFUL",
     "Largest RCT on recruitment n=1010, P=0.041, definitive harm signal, 120 ICUs, 9 countries"),

    ("Aggressive recruitment maneuver strategy","POI_intervention",
     "poi:causesAdverseOutcome",
     "Barotrauma",                          "POI_outcome",
     "28973363","ART",2017, 0.92, "5.6% vs 1.6%","HARMFUL",
     "Secondary endpoint, P=0.001, mechanistically plausible, consistent with harm signal"),

    ("Dexamethasone",                   "POI_intervention",
     "poi:treats",
     "Acute respiratory distress syndrome", "POI_disease",
     "32043986","DEXA-ARDS",2020, 0.82, "60d mort 21% vs 36%","BENEFICIAL",
     "RCT n=277, unblinded (-0.05), stopped early (-0.05), P=0.005, converges with RECOVERY"),

    ("Dexamethasone",                   "POI_intervention",
     "poi:hasClinicalOutcome",
     "Ventilator-free days at day 28",      "POI_outcome",
     "32043986","DEXA-ARDS",2020, 0.85, "+4.8 days (2.57-7.03)","BENEFICIAL",
     "Primary endpoint, P<0.0001, clear biological mechanism"),

    ("Dexamethasone",                   "POI_intervention",
     "poi:hasClinicalOutcome",
     "60-day all-cause mortality",          "POI_outcome",
     "32043986","DEXA-ARDS",2020, 0.82, "21% vs 36% P=0.005","BENEFICIAL",
     "Secondary endpoint, unblinded trial, stopped early — may overestimate"),

    ("Cisatracurium",                   "POI_intervention",
     "poi:hasClinicalOutcome",
     "90-day all-cause mortality",          "POI_outcome",
     "31112383","ROSE",2019, 0.90, "42.5% vs 42.8% P=0.93","NULL",
     "Large RCT n=1006, stopped futility at 2nd interim, definitive null in lighter sedation era"),

    ("Cisatracurium",                   "POI_intervention",
     "poi:hasTemporalContext",
     "Lighter sedation era clinical context","POI_context",
     "31112383","ROSE",2019, 0.90, "","NULL",
     "Explicit temporal context: NMB null in ROSE (2019) vs beneficial in ACURASYS (2010, deep sedation)"),

    ("VV-ECMO",                         "POI_intervention",
     "poi:treats",
     "Acute respiratory distress syndrome", "POI_disease",
     "29791822","EOLIA",2018, 0.65, "RR 0.76 (0.55-1.04)","TREND",
     "RCT n=249, P=0.09 (non-significant), 28% crossover (-0.15), Bayesian ~88% prob benefit (+0.05)"),

    ("VV-ECMO",                         "POI_intervention",
     "poi:hasClinicalOutcome",
     "60-day all-cause mortality",          "POI_outcome",
     "29791822","EOLIA",2018, 0.65, "35% vs 46% P=0.09","TREND",
     "Primary endpoint missed P=0.09, high crossover contamination, Bayesian reanalysis supports benefit"),

    ("Conservative oxygen therapy",     "POI_intervention",
     "poi:causesAdverseOutcome",
     "Mesenteric ischemia in ARDS",         "POI_outcome",
     "32160661","LOCO2",2020, 0.85, "5 events vs 0","HARMFUL",
     "Stopped early safety, mesenteric ischemia 5 vs 0 events, biologically plausible, underpowered overall"),

    ("High-flow nasal cannula oxygen therapy","POI_intervention",
     "poi:hasClinicalOutcome",
     "90-day all-cause mortality",          "POI_outcome",
     "25981908","FLORALI",2015, 0.68, "12% vs 23-28% P=0.02","MIXED",
     "Discordant primary null/secondary beneficial; subgroup PaO2/FiO2<=200 drives effect (-0.10)"),

    ("Individualized OLA",              "POI_intervention",
     "poi:treats",
     "One-lung ventilation surgical context","POI_context",
     "38065200","iPROVE-OLV",2023, 0.93, "RR 0.39 (0.28-0.56)","BENEFICIAL",
     "Large RCT n=1308, P<0.001, NNT=11, multi-component but strong signal"),

    ("High PEEP ventilation strategy",  "POI_intervention",
     "poi:isContraindicatedIn",
     "Focal ARDS",                          "POI_disease",
     "28973363","ART",2017, 0.88, "","HARMFUL",
     "Mechanistic + ART harm signal; focal ARDS lower recruitability, overdistension risk"),

    ("High PEEP ventilation strategy",  "POI_intervention",
     "poi:isContraindicatedIn",
     "Right ventricular dysfunction ARDS population","POI_population",
     "28973363","ART",2017, 0.88, "","HARMFUL",
     "High PEEP increases RV afterload; ~20-30% ARDS incidence; consistent across multiple studies"),

    ("Aggressive recruitment maneuver strategy","POI_intervention",
     "poi:isContraindicatedIn",
     "Focal ARDS",                          "POI_disease",
     "28973363","ART",2017, 0.92, "","HARMFUL",
     "ART definitive harm + focal ARDS lower recruitability = double contraindication"),

    # Biomarker assertions
    ("Acute respiratory distress syndrome","POI_disease",
     "poi:hasBiomarker",
     "Interleukin-6",                       "POI_biomarker",
     "33253239","Calfee2021",2021, 0.90, "elevated in hyperinflammatory","BENEFICIAL",
     "Meta-analysis of subphenotype studies, consistently elevated in hyperinflammatory ARDS"),

    ("Acute respiratory distress syndrome","POI_disease",
     "poi:hasBiomarker",
     "Interleukin-8",                       "POI_biomarker",
     "33253239","Calfee2021",2021, 0.90, "elevated in hyperinflammatory","BENEFICIAL",
     "Parsimonious classifier variable; consistently elevated in hyperinflammatory subphenotype"),

    ("Acute respiratory distress syndrome","POI_disease",
     "poi:hasBiomarker",
     "Soluble receptor for advanced glycation end-products","POI_biomarker",
     "33253239","Calfee2021",2021, 0.88, "AT1 cell injury marker","BENEFICIAL",
     "sRAGE reflects alveolar epithelial injury; validated biomarker across multiple cohorts"),

    ("Acute respiratory distress syndrome","POI_disease",
     "poi:hasBiomarker",
     "Angiopoietin-2",                      "POI_biomarker",
     "33253239","Calfee2021",2021, 0.88, "endothelial permeability","BENEFICIAL",
     "Endothelial injury marker; elevated in hyperinflammatory; predicts mortality"),

    ("Hyperinflammatory ARDS subphenotype","POI_disease",
     "poi:hasBiomarker",
     "Interleukin-6",                       "POI_biomarker",
     "33253239","Calfee2021",2021, 0.95, "elevated","BENEFICIAL",
     "Core classifier variable in parsimonious 3-variable model; AUC 0.94"),

    ("Hyperinflammatory ARDS subphenotype","POI_disease",
     "poi:hasBiomarker",
     "Plasminogen activator inhibitor-1",   "POI_biomarker",
     "33253239","Calfee2021",2021, 0.95, "elevated","BENEFICIAL",
     "PAI-1 elevated in hyperinflammatory; procoagulant state marker; validated in ALVEOLI, FACTT"),

    ("Hyperinflammatory ARDS subphenotype","POI_disease",
     "poi:hasDifferentialResponse",
     "Conservative fluid management strategy","POI_intervention",
     "25030514","Calfee2014",2014, 0.85, "mortality benefit in hyper","BENEFICIAL",
     "HTE analysis FACTT: conservative fluid benefits hyperinflammatory but not hypoinflammatory"),
]


# ── Load sources ──────────────────────────────────────────────────────────────

def load_gold():
    edges, nodes = [], {}
    for row in GOLD_EDGES:
        (s_lbl, s_sub, pred, o_lbl, o_sub,
         pmid, trial, year, conf, effect, direction, rationale) = row

        e = Edge(s_lbl, pred, o_lbl,
                 subj_subset=s_sub, obj_subset=o_sub,
                 pmid=pmid, trial=trial, year=year,
                 confidence=conf, effect_size=effect,
                 direction=direction,
                 confidence_rationale=rationale)
        edges.append(e)

        if s_lbl not in nodes:
            nodes[s_lbl] = Node(s_lbl, s_sub, pmid=pmid)
        if o_lbl not in nodes:
            nodes[o_lbl] = Node(o_lbl, o_sub)

    print(f"  Gold: {len(edges)} expert-verified edges (with confidence rationale)")
    return edges, nodes


def load_from_owl():
    edges, nodes = [], {}
    if not OWL_FILE.exists():
        print(f"  WARNING: {OWL_FILE} not found")
        return edges, nodes
    try:
        from rdflib import Graph, Namespace, RDF, RDFS, OWL
        g = Graph()
        g.parse(str(OWL_FILE))
        OBO      = Namespace("http://purl.obolibrary.org/obo/")
        OBOINOWL = Namespace("http://www.geneontology.org/formats/oboInOwl#")

        label_idx  = {}
        subset_idx = {}
        for cls in g.subjects(RDF.type, OWL.Class):
            lbl = next(g.objects(cls, RDFS.label), None)
            if lbl:
                label_idx[str(cls)] = str(lbl)
                for s in g.objects(cls, OBO.inSubset):
                    subset_idx[str(cls)] = str(s).split("/")[-1]

        for prop in g.subjects(RDF.type, OWL.ObjectProperty):
            p_lbl = str(next(g.objects(prop, RDFS.label),"")).replace(" ","")
            p_curie = f"poi:{p_lbl}"
            for subj, _, obj in g.triples((None, prop, None)):
                s_lbl = label_idx.get(str(subj), str(subj).split("/")[-1])
                o_lbl = label_idx.get(str(obj),  str(obj).split("/")[-1])
                s_sub = subset_idx.get(str(subj), "POI_unknown")
                o_sub = subset_idx.get(str(obj),  "POI_unknown")
                pmid_raw = str(next(g.objects(subj, OBO.source_pmid),""))
                pmid  = pmid_raw.split()[0] if pmid_raw.strip() else ""
                edges.append(Edge(s_lbl, p_curie, o_lbl,
                                  subj_subset=s_sub, obj_subset=o_sub,
                                  pmid=pmid, confidence=0.90,
                                  confidence_rationale="OWL ontology assertion — expert curated"))
                if s_lbl not in nodes:
                    nodes[s_lbl] = Node(s_lbl, s_sub, iri=str(subj), pmid=pmid)
                if o_lbl not in nodes:
                    nodes[o_lbl] = Node(o_lbl, o_sub, iri=str(obj))

    except ImportError:
        print("  WARNING: rdflib not installed")
    print(f"  OWL: {len(edges)} edges")
    return edges, nodes


def load_from_jsonl():
    edges, nodes = [], {}
    if not MASTER.exists():
        print(f"  WARNING: {MASTER} not found")
        return edges, nodes

    with open(MASTER) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
            except Exception:
                continue

            pmid  = str(rec.get("_source_pmid","")).strip()
            study = rec.get("study", {})
            year  = study.get("year",0) if isinstance(study,dict) else 0
            trial = study.get("acronym","") if isinstance(study,dict) else ""

            interv = rec.get("intervention",{})
            compar = rec.get("comparator",{})
            outc   = rec.get("outcome_effect",{})
            pop    = rec.get("population_context",{})

            if not isinstance(interv, dict): continue

            i_name = (interv.get("name","") or interv.get("label","") or
                      interv.get("description","")).strip()
            o_name = (outc.get("outcome_name","") or
                      outc.get("primary_outcome","") or
                      outc.get("outcome_label","")).strip() if isinstance(outc,dict) else ""
            cond   = (pop.get("condition","") or
                      pop.get("diagnosis","")).strip() if isinstance(pop,dict) else ""
            direction = (outc.get("direction","") or
                         outc.get("result_direction","")).upper() if isinstance(outc,dict) else ""
            effect = str(outc.get("effect_size","") or
                         outc.get("effect_measure","")) if isinstance(outc,dict) else ""
            pval   = str(outc.get("p_value","")) if isinstance(outc,dict) else ""

            if not i_name or not o_name: continue

            pred = DIRECTION_TO_PRED.get(direction, "poi:hasClinicalOutcome")
            conf = DIRECTION_TO_CONF.get(direction, 0.60)
            rationale = f"Haiku-extracted from PMID:{pmid}, direction={direction}"

            edges.append(Edge(i_name, pred, o_name,
                              subj_subset="POI_intervention",
                              obj_subset="POI_outcome",
                              pmid=pmid, trial=trial, year=year,
                              confidence=conf, effect_size=effect,
                              p_value=pval, direction=direction,
                              confidence_rationale=rationale))

            if cond:
                edges.append(Edge(i_name, "poi:treats", cond,
                                  subj_subset="POI_intervention",
                                  obj_subset="POI_disease",
                                  pmid=pmid, trial=trial, year=year,
                                  confidence=conf * 0.9,
                                  confidence_rationale=rationale))

            for lbl, sub in [(i_name,"POI_intervention"),
                              (o_name,"POI_outcome"),
                              (cond,"POI_disease")]:
                if lbl and lbl not in nodes:
                    nodes[lbl] = Node(lbl, sub, pmid=pmid)

            # poi_graph_edges
            for edge in (rec.get("poi_graph_edges",[]) or []):
                if not isinstance(edge, dict): continue
                fn = edge.get("from_node","") or edge.get("subject","")
                tn = edge.get("to_node","")   or edge.get("object","")
                rel= edge.get("relation","")  or "hasClinicalOutcome"
                ec = parse_confidence(edge.get("confidence",0.65))
                ed = edge.get("direction","")
                fs = edge.get("from_subset","") or infer_subset(fn)
                ts = edge.get("to_subset","")   or infer_subset(tn)
                ep = DIRECTION_TO_PRED.get(ed.upper() if ed else "",
                                           f"poi:{rel}" if rel else "poi:hasClinicalOutcome")
                if fn and tn:
                    edges.append(Edge(fn, ep, tn,
                                      subj_subset=fs, obj_subset=ts,
                                      pmid=pmid, trial=trial, year=year,
                                      confidence=ec, direction=ed,
                                      confidence_rationale=f"Haiku poi_graph_edges, PMID:{pmid}"))
                    for lbl, sub in [(fn,fs),(tn,ts)]:
                        if lbl and lbl not in nodes:
                            nodes[lbl] = Node(lbl, sub, pmid=pmid)

    print(f"  JSONL: {len(edges)} edges from {MASTER.name}")
    return edges, nodes


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(edges):
    seen = {}
    for e in edges:
        key = (e.subj_label.lower(), e.predicate, e.obj_label.lower())
        if key not in seen:
            seen[key] = e
        else:
            ex = seen[key]
            if e.confidence > ex.confidence:
                ex.confidence = e.confidence
                if e.effect_size: ex.effect_size = e.effect_size
                if e.p_value:     ex.p_value     = e.p_value
                if e.confidence_rationale:
                    ex.confidence_rationale = e.confidence_rationale
            if e.pmid and e.pmid not in ex.pmid:
                ex.pmid = (ex.pmid + "|" + e.pmid) if ex.pmid else e.pmid
            if e.trial and not ex.trial:
                ex.trial = e.trial
            if e.year and not ex.year:
                ex.year = e.year
    return list(seen.values())


# ── Writers ───────────────────────────────────────────────────────────────────

def write_nodes_csv(nodes, path):
    fieldnames = ["node_id","label","subset","node_type","iri","pmid"]
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for lbl, node in sorted(nodes.items()):
            w.writerow(node.to_dict())
    print(f"  ✓ Nodes: {path} ({len(nodes)} rows)")


def write_edges_csv(edges, path):
    fieldnames = [
        "edge_id","subject","subject_type","subject_subset",
        "predicate","biolink_predicate",
        "object","object_type","object_subset",
        "pmid","trial","year","confidence","confidence_rationale",
        "effect_size","p_value","direction","temporal_context"
    ]
    with open(path,"w",newline="",encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in sorted(edges, key=lambda x: -x.confidence):
            w.writerow(e.to_dict())
    print(f"  ✓ Edges: {path} ({len(edges)} rows)")


def write_ttl(edges, nodes, path):
    lines = [
        f"# POI-KB ARDS Knowledge Graph — RDF Turtle",
        f"# Generated: {datetime.utcnow().isoformat()}Z",
        f"# Nodes: {len(nodes)} | Edges: {len(edges)}",
        f"",
        f"@prefix poi:      <http://poi-kb.org/ards/> .",
        f"@prefix biolink:  <https://w3id.org/biolink/vocab/> .",
        f"@prefix obo:      <http://purl.obolibrary.org/obo/> .",
        f"@prefix rdf:      <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        f"@prefix rdfs:     <http://www.w3.org/2000/01/rdf-schema#> .",
        f"@prefix owl:      <http://www.w3.org/2002/07/owl#> .",
        f"@prefix xsd:      <http://www.w3.org/2001/XMLSchema#> .",
        f"",
    ]
    for lbl, node in sorted(nodes.items()):
        slug = re.sub(r"[^a-z0-9_]","_",lbl.lower())[:50]
        iri  = node.iri
        lbl_e = lbl.replace('"','\\"')
        lines += [
            f"<{iri}>",
            f'  a owl:Class, {node.node_type} ;',
            f'  rdfs:label "{lbl_e}"@en ;',
            f'  obo:inSubset poi:{node.subset} ;',
            f'  .',f"",
        ]
    for e in edges:
        sn = nodes.get(e.subj_label)
        on = nodes.get(e.obj_label)
        si = sn.iri if sn else f"http://poi-kb.org/ards/kg/{re.sub(r'[^a-z0-9]','_',e.subj_label.lower())[:40]}"
        oi = on.iri if on else f"http://poi-kb.org/ards/kg/{re.sub(r'[^a-z0-9]','_',e.obj_label.lower())[:40]}"
        prop = e.predicate.split(":")[-1]
        ei   = f"http://poi-kb.org/ards/kg/stmt_{re.sub(r'[^a-z0-9]','_',e.subj_label.lower())[:20]}__{prop}__{re.sub(r'[^a-z0-9]','_',e.obj_label.lower())[:20]}__{e.pmid[:8] if e.pmid else 'x'}"
        lines += [
            f"<{si}> poi:{prop} <{oi}> .",
            f"<{ei}>",
            f"  a rdf:Statement ;",
            f"  rdf:subject   <{si}> ;",
            f"  rdf:predicate poi:{prop} ;",
            f"  rdf:object    <{oi}> ;",
            f'  biolink:provided_by "POI-KB" ;',
        ]
        if e.pmid:      lines.append(f'  poi:sourcePMID "{e.pmid}" ;')
        if e.trial:     lines.append(f'  poi:trialAcronym "{e.trial}" ;')
        if e.year:      lines.append(f'  poi:trialYear {e.year} ;')
        lines.append(    f'  poi:confidence {e.confidence:.3f} ;')
        if e.effect_size: lines.append(f'  poi:effectSize "{e.effect_size.replace(chr(34),chr(92)+chr(34))}" ;')
        if e.direction:   lines.append(f'  poi:direction "{e.direction}" ;')
        lines += ["  .", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ TTL: {path} ({len(Path(path).read_bytes())//1024} KB)")


def write_stats(edges, nodes, path):
    subset_c = Counter(n.subset for n in nodes.values())
    type_c   = Counter(n.node_type for n in nodes.values())
    pred_c   = Counter(e.predicate for e in edges)
    bl_pred_c= Counter(e.biolink_pred for e in edges)
    dir_c    = Counter(e.direction for e in edges if e.direction)
    years    = [e.year for e in edges if e.year]
    conf_hi  = sum(1 for e in edges if e.confidence >= 0.85)
    conf_med = sum(1 for e in edges if 0.65 <= e.confidence < 0.85)
    conf_lo  = sum(1 for e in edges if e.confidence < 0.65)
    stats = {
        "generated": datetime.utcnow().isoformat()+"Z",
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "nodes_by_subset": dict(subset_c),
        "nodes_by_biolink_type": dict(type_c),
        "edges_by_predicate": dict(pred_c.most_common()),
        "edges_by_biolink_predicate": dict(bl_pred_c.most_common()),
        "edges_by_direction": dict(dir_c),
        "confidence": {
            "high_>=0.85": conf_hi,
            "medium_0.65-0.85": conf_med,
            "low_<0.65": conf_lo,
            "definition": "Composite curator-assigned score: trial design + effect size + replication + crossover"
        },
        "year_range": f"{min(years)}–{max(years)}" if years else "N/A",
        "unique_pmids": len({p for e in edges for p in e.pmid.split("|") if p}),
        "unique_trials": len({e.trial for e in edges if e.trial}),
    }
    Path(path).write_text(json.dumps(stats, indent=2))
    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all",
                    choices=["jsonl","owl","gold","all"])
    ap.add_argument("--format", default="all",
                    choices=["csv","ttl","all"])
    ap.add_argument("--min-confidence", type=float, default=0.0)
    args = ap.parse_args()

    print(f"\n{'='*60}")
    print("POI-KB ARDS Knowledge Graph Builder v2 (BioLink aligned)")
    print(f"{'='*60}")
    print(f"Source: {args.source}  |  Format: {args.format}\n")

    all_edges, all_nodes = [], {}

    if args.source in ("gold","all"):
        e,n = load_gold();   all_edges.extend(e); all_nodes.update(n)
    if args.source in ("owl","all"):
        e,n = load_from_owl(); all_edges.extend(e); all_nodes.update(n)
    if args.source in ("jsonl","all"):
        e,n = load_from_jsonl(); all_edges.extend(e); all_nodes.update(n)

    print(f"\nRaw: {len(all_edges)} edges | {len(all_nodes)} nodes")
    all_edges = deduplicate(all_edges)
    print(f"Deduped: {len(all_edges)} edges")

    if args.min_confidence > 0:
        all_edges = [e for e in all_edges if e.confidence >= args.min_confidence]
        print(f"Filtered (>={args.min_confidence}): {len(all_edges)} edges")

    # Ensure all nodes exist
    for e in all_edges:
        if e.subj_label not in all_nodes:
            all_nodes[e.subj_label] = Node(e.subj_label, e.subj_subset)
        if e.obj_label not in all_nodes:
            all_nodes[e.obj_label]  = Node(e.obj_label,  e.obj_subset)

    print(f"\nWriting outputs to {OUT_DIR}/")
    if args.format in ("csv","all"):
        write_nodes_csv(all_nodes, OUT_DIR/"poi_graph_nodes.csv")
        write_edges_csv(all_edges, OUT_DIR/"poi_graph_edges.csv")
    if args.format in ("ttl","all"):
        write_ttl(all_edges, all_nodes, OUT_DIR/"poi_graph.ttl")

    stats = write_stats(all_edges, all_nodes, OUT_DIR/"poi_graph_stats.json")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Nodes: {stats['total_nodes']}  Edges: {stats['total_edges']}")
    print(f"PMIDs: {stats['unique_pmids']}  Trials: {stats['unique_trials']}")
    print(f"\nNodes by BioLink type:")
    for k,v in sorted(stats['nodes_by_biolink_type'].items(),key=lambda x:-x[1]):
        print(f"  {k:50s}: {v}")
    print(f"\nTop edges by BioLink predicate:")
    for k,v in list(stats['edges_by_biolink_predicate'].items())[:6]:
        print(f"  {k:45s}: {v}")
    print(f"\nConfidence: high={stats['confidence']['high_>=0.85']} "
          f"medium={stats['confidence']['medium_0.65-0.85']} "
          f"low={stats['confidence']['low_<0.65']}")


if __name__ == "__main__":
    main()
