"""WDP seed: deterministic synthetic research-world data for db `wdp`, keyed
on rfff_seed ORCIDs/UEIs so cross-boundary demo queries actually join
(docs/specs/data-model.md, docs/specs/fake-wdp.md).

Usage: python scripts/seed_wdp.py [--dsn postgresql://...]
       [--rfff-dsn postgresql://...] [--deny-orcids 0000-...,0000-...]
--dsn defaults to $WDP_DATABASE_URL, --rfff-dsn to $RFFF_SEED_DATABASE_URL,
--deny-orcids to $FAKE_WDP_DENY_ORCIDS (comma list). Stages: read rfff_seed ->
plan gaps (a fraction of ORCIDs get NO WDP records at all; absence = honest
gap) -> generate persons/entities/documents -> verify deny ORCIDs (denial must
be distinguishable from absence) -> select demo anchors -> load. Fully
deterministic: fixed RNG seed, sorted inputs, RNG-derived uuids.
"""
import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass

RNG_SEED = 46170  # fixed: same rfff_seed inputs -> byte-identical wdp seed
GAP_FRACTION = 0.15  # fraction of non-denied ORCIDs left without WDP records

# Template pools for synthetic content (deterministic picks via the rng).
PUB_TOPICS = [
    "Quantum Sensing Arrays", "Hypersonic Boundary Layers",
    "Synthetic Genome Assembly", "Autonomous Swarm Coordination",
    "Photonic Interconnects", "Cryogenic Control Electronics",
    "Neuromorphic Inference", "Additive Alloy Fatigue",
    "Microbiome Signal Transduction", "Terahertz Imaging",
]
PUB_KINDS = [
    "A Survey", "Field Results", "A Bayesian Approach", "Design Principles",
    "Failure Modes", "A Benchmark Study", "Scaling Laws", "An Open Dataset",
]
PUB_VENUES = [
    "Journal of Applied Research", "Proceedings of the Frontier Symposium",
    "Letters in Computational Science", "Annals of Experimental Methods",
    "Transactions on Emerging Systems",
]
FUNDERS = [
    "National Science Endowment", "Defense Research Council",
    "Energy Futures Agency", "Global Health Trust",
]
REGISTRIES = [
    "National Business Registry", "Trade Compliance Index",
    "Federal Award Registry",
]
ENTITY_STATUSES = ["active", "inactive", "under_review"]
COUNTRIES = [
    "United States", "Canada", "United Kingdom", "Germany", "Japan",
    "Australia",
]

YEAR_MIN, YEAR_MAX = 2015, 2025
PUBS_PER_PERSON = (2, 12)
FUNDING_PER_PERSON = (0, 4)
RECORDS_PER_ENTITY = (1, 8)


@dataclass
class PersonInput:
    orcid: str
    name: str
    affiliations: list[str]


@dataclass
class EntityInput:
    uei: str
    name: str


@dataclass
class Generated:
    persons: list[dict]
    entities: list[dict]
    documents: list[dict]


@dataclass
class Anchors:
    query2_proposal_number: str
    query3b_person_name: str
    query3b_denied_orcid: str


# --- plan gaps ----------------------------------------------------------------


def plan_gaps(
    orcids: list[str],
    deny_orcids: list[str],
    rng: random.Random,
    fraction: float = GAP_FRACTION,
) -> set[str]:
    """Pick the ORCIDs that get NO WDP records at all (honest gaps). Denied
    ORCIDs are never eligible: denial must be distinguishable from absence."""
    eligible = sorted(set(orcids) - set(deny_orcids))
    return set(rng.sample(eligible, round(len(eligible) * fraction)))


# --- generate -------------------------------------------------------------------


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128))


def generate(
    persons: list[PersonInput],
    entities: list[EntityInput],
    gaps: set[str],
    rng: random.Random,
) -> Generated:
    """Synthesize persons/entities/documents. Gap ORCIDs get no persons row at
    all -> fake-wdp returns empty results (honest gap, never not_found).
    Deterministic: inputs sorted, all randomness from the passed rng."""
    person_rows: list[dict] = []
    entity_rows: list[dict] = []
    documents: list[dict] = []

    for person in sorted(persons, key=lambda p: p.orcid):
        if person.orcid in gaps:
            continue
        ref_id = _uuid(rng)
        person_rows.append(
            {
                "ref_id": ref_id,
                "orcid": person.orcid,
                "name": person.name,
                "affiliations": sorted(person.affiliations),
            }
        )
        for _ in range(rng.randint(*PUBS_PER_PERSON)):
            topic = rng.choice(PUB_TOPICS)
            venue = rng.choice(PUB_VENUES)
            documents.append(
                {
                    "doc_id": _uuid(rng),
                    "ref_id": ref_id,
                    "type": "publication",
                    "title": f"{topic}: {rng.choice(PUB_KINDS)}",
                    "year": rng.randint(YEAR_MIN, YEAR_MAX),
                    "source": venue,
                    "detail": {
                        "abstract": (
                            f"Findings on {topic.lower()} across "
                            f"{rng.randint(3, 40)} trials."
                        ),
                        "venue": venue,
                        "coauthor_count": rng.randint(0, 11),
                    },
                }
            )
        for _ in range(rng.randint(*FUNDING_PER_PERSON)):
            topic = rng.choice(PUB_TOPICS)
            funder = rng.choice(FUNDERS)
            documents.append(
                {
                    "doc_id": _uuid(rng),
                    "ref_id": ref_id,
                    "type": "funding_record",
                    "title": f"{funder} award: {topic}",
                    "year": rng.randint(YEAR_MIN, YEAR_MAX),
                    "source": funder,
                    "detail": {
                        "amount": rng.randint(50, 2000) * 1000,
                        "currency": "USD",
                        "funder": funder,
                    },
                }
            )

    for entity in sorted(entities, key=lambda e: e.uei):
        ref_id = _uuid(rng)
        entity_rows.append(
            {
                "ref_id": ref_id,
                "uei": entity.uei,
                "name": entity.name,
                "country": rng.choice(COUNTRIES),
            }
        )
        for _ in range(rng.randint(*RECORDS_PER_ENTITY)):
            registry = rng.choice(REGISTRIES)
            documents.append(
                {
                    "doc_id": _uuid(rng),
                    "ref_id": ref_id,
                    "type": "entity_record",
                    "title": f"{registry} record: {entity.name}",
                    "year": rng.randint(YEAR_MIN, YEAR_MAX),
                    "source": registry,
                    "detail": {
                        "registry": registry,
                        "status": rng.choice(ENTITY_STATUSES),
                    },
                }
            )

    return Generated(persons=person_rows, entities=entity_rows,
                     documents=documents)


# --- verify deny + anchors ------------------------------------------------------


def verify_deny_orcids(
    deny: list[str], rfff_orcids: list[str], gaps: set[str]
) -> list[str]:
    """FAKE_WDP_DENY_ORCIDS must name ORCIDs that exist in rfff_seed AND get
    WDP records: a 403 for an absent person would be indistinguishable from a
    gap (docs/specs/fake-wdp.md)."""
    known = set(rfff_orcids)
    errors = []
    for orcid in sorted(set(deny)):
        if orcid not in known:
            errors.append(
                f"denied ORCID {orcid} does not exist in rfff_seed personnel"
            )
        elif orcid in gaps:
            errors.append(
                f"denied ORCID {orcid} is a planned WDP gap; denial must be "
                f"distinguishable from absence"
            )
    return errors


def select_anchors(
    proposal_personnel: list[tuple[str, str]],
    persons_by_orcid: dict[str, str],
    gaps: set[str],
    deny: list[str],
) -> Anchors:
    """Demo anchors (read back by `make demo`): Query 2 needs a proposal whose
    personnel include >=1 ORCID with WDP records (and not denied) AND >=1 gap
    ORCID; Query 3B is the first sorted denied ORCID + that person's name."""
    if not deny:
        raise ValueError("cannot select anchors: deny ORCID list is empty")
    denied = sorted(set(deny))[0]
    if denied not in persons_by_orcid:
        raise ValueError(
            f"cannot select anchors: denied ORCID {denied} has no rfff_seed "
            f"personnel row"
        )

    deny_set = set(deny)
    members: dict[str, set[str]] = {}
    for proposal_number, orcid in proposal_personnel:
        members.setdefault(proposal_number, set()).add(orcid)
    for proposal_number in sorted(members):
        orcids = members[proposal_number]
        has_records = any(
            o in persons_by_orcid and o not in gaps and o not in deny_set
            for o in orcids
        )
        has_gap = any(o in gaps for o in orcids)
        if has_records and has_gap:
            return Anchors(
                query2_proposal_number=proposal_number,
                query3b_person_name=persons_by_orcid[denied],
                query3b_denied_orcid=denied,
            )
    raise ValueError(
        "no proposal qualifies as the Query-2 anchor (needs >=1 personnel "
        "ORCID with WDP records and not denied, plus >=1 gap ORCID); adjust "
        "GAP_FRACTION or RNG_SEED"
    )


# --- report ---------------------------------------------------------------------


def render_report(
    gen: Generated,
    gaps: set[str],
    anchors: Anchors,
    load_counts: dict[str, int],
) -> str:
    by_type = {"publication": 0, "funding_record": 0, "entity_record": 0}
    for doc in gen.documents:
        by_type[doc["type"]] += 1
    examples = ", ".join(sorted(gaps)[:5]) or "(none)"
    lines = [
        "WDP seed report",
        "===============",
        f"persons (with WDP records): {len(gen.persons)}",
        f"entities:                   {len(gen.entities)}",
        f"documents:                  {len(gen.documents)}",
    ]
    lines += [f"  {t:<20} {n:>5}" for t, n in sorted(by_type.items())]
    lines += [
        "",
        "deliberate gaps (ORCIDs with NO WDP records; honest absence)",
        "-------------------------------------------------------------",
        f"gap count: {len(gaps)}  e.g. {examples}",
        "",
        "denied ORCID(s) (fake-wdp returns 403, distinct from absence)",
        "--------------------------------------------------------------",
        f"  {anchors.query3b_denied_orcid}",
        "",
        "demo anchors (demo_anchors table; read by `make demo`)",
        "-------------------------------------------------------",
        f"  query2_proposal_number: {anchors.query2_proposal_number}",
        f"  query3b_person_name:    {anchors.query3b_person_name}",
        f"  query3b_denied_orcid:   {anchors.query3b_denied_orcid}",
        "",
        "loaded",
        "------",
    ]
    lines += [f"  {table:<15} {n:>5}" for table, n in load_counts.items()]
    return "\n".join(lines)


# --- load (thin; exercised by `make seed`, not unit tests) ----------------------


async def load(dsn: str, gen: Generated, anchors: Anchors) -> dict[str, int]:
    import asyncpg  # deferred so the pure stages stay importable without it

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # Idempotent re-seed: children first (documents references both
            # dimension tables by ref_id).
            for table in ("documents", "persons", "entities", "demo_anchors"):
                await conn.execute(f"DELETE FROM {table}")

            # asyncpg accepts uuid.UUID for uuid columns; jsonb needs an
            # explicit json.dumps + ::jsonb cast (no codec configured).
            await conn.executemany(
                "INSERT INTO persons (ref_id, orcid, name, affiliations) "
                "VALUES ($1, $2, $3, $4)",
                [(p["ref_id"], p["orcid"], p["name"], p["affiliations"])
                 for p in gen.persons],
            )
            await conn.executemany(
                "INSERT INTO entities (ref_id, uei, name, country) "
                "VALUES ($1, $2, $3, $4)",
                [(e["ref_id"], e["uei"], e["name"], e["country"])
                 for e in gen.entities],
            )
            await conn.executemany(
                "INSERT INTO documents (doc_id, ref_id, type, title, year, "
                "source, detail) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                [(d["doc_id"], d["ref_id"], d["type"], d["title"], d["year"],
                  d["source"], json.dumps(d["detail"]))
                 for d in gen.documents],
            )
            await conn.executemany(
                "INSERT INTO demo_anchors (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                [
                    ("query2_proposal_number", anchors.query2_proposal_number),
                    ("query3b_person_name", anchors.query3b_person_name),
                    ("query3b_denied_orcid", anchors.query3b_denied_orcid),
                ],
            )
    finally:
        await conn.close()
    return {
        "persons": len(gen.persons),
        "entities": len(gen.entities),
        "documents": len(gen.documents),
        "demo_anchors": 3,
    }


# --- rfff_seed inputs -----------------------------------------------------------


async def read_rfff(
    dsn: str,
) -> tuple[list[PersonInput], list[EntityInput], list[tuple[str, str]]]:
    """Read the join keys from rfff_seed: person ORCIDs + full names,
    affiliations per ORCID, distinct submitting entities, and the
    (proposal_number, person_orcid) pairs for anchor selection."""
    import asyncpg  # deferred so the pure stages stay importable without it

    conn = await asyncpg.connect(dsn)
    try:
        person_rows = await conn.fetch(
            "SELECT person_orcid, first_name, middle_name, last_name "
            "FROM personnel ORDER BY person_orcid"
        )
        affiliation_rows = await conn.fetch(
            "SELECT DISTINCT person_orcid, affiliation_name "
            "FROM proposal_personnel WHERE affiliation_name IS NOT NULL "
            "ORDER BY person_orcid, affiliation_name"
        )
        entity_rows = await conn.fetch(
            "SELECT DISTINCT submitting_entity_uei, submitting_entity_name "
            "FROM proposals WHERE submitting_entity_uei IS NOT NULL "
            "AND submitting_entity_uei <> '' "
            "ORDER BY submitting_entity_uei, submitting_entity_name"
        )
        junction_rows = await conn.fetch(
            "SELECT proposal_number, person_orcid FROM proposal_personnel "
            "ORDER BY proposal_number, person_orcid"
        )
    finally:
        await conn.close()

    affiliations: dict[str, list[str]] = {}
    for r in affiliation_rows:
        affiliations.setdefault(r["person_orcid"], []).append(
            r["affiliation_name"])
    persons = [
        PersonInput(
            orcid=r["person_orcid"],
            name=" ".join(
                part for part in (r["first_name"], r["middle_name"],
                                  r["last_name"]) if part
            ),
            affiliations=affiliations.get(r["person_orcid"], []),
        )
        for r in person_rows
    ]
    entities_by_uei: dict[str, EntityInput] = {}
    for r in entity_rows:  # first (sorted) name wins per UEI
        entities_by_uei.setdefault(
            r["submitting_entity_uei"],
            EntityInput(uei=r["submitting_entity_uei"],
                        name=r["submitting_entity_name"] or ""),
        )
    pairs = [(r["proposal_number"], r["person_orcid"]) for r in junction_rows]
    return persons, list(entities_by_uei.values()), pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("WDP_DATABASE_URL"),
        help="wdp DSN (default: $WDP_DATABASE_URL)",
    )
    parser.add_argument(
        "--rfff-dsn",
        default=os.environ.get("RFFF_SEED_DATABASE_URL"),
        help="rfff_seed DSN (default: $RFFF_SEED_DATABASE_URL)",
    )
    parser.add_argument(
        "--deny-orcids",
        default=os.environ.get("FAKE_WDP_DENY_ORCIDS", ""),
        help="comma list of ORCIDs fake-wdp denies "
             "(default: $FAKE_WDP_DENY_ORCIDS)",
    )
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("no wdp DSN: pass --dsn or set WDP_DATABASE_URL")
    if not args.rfff_dsn:
        sys.exit("no rfff_seed DSN: pass --rfff-dsn or set "
                 "RFFF_SEED_DATABASE_URL")

    persons, entities, pairs = asyncio.run(read_rfff(args.rfff_dsn))
    if not persons:
        sys.exit("rfff_seed has no personnel; run scripts/seed_rfff.py first")
    orcids = sorted(p.orcid for p in persons)

    deny = [o.strip() for o in args.deny_orcids.split(",") if o.strip()]
    if not deny:
        # Deterministic pick BEFORE gap planning: deny ORCIDs are excluded
        # from gap eligibility, so this ORCID is with-records by construction
        # and a re-run with FAKE_WDP_DENY_ORCIDS set to it is identical.
        deny = [orcids[0]]
        print(
            "\n"
            "############################################################\n"
            "##  FAKE_WDP_DENY_ORCIDS is not set.                      ##\n"
            "##  Deterministically selected the denied ORCID:          ##\n"
            f"##    {deny[0]:<52}##\n"
            "##  fake-wdp only denies what ITS env lists — set         ##\n"
            f"##    FAKE_WDP_DENY_ORCIDS={deny[0]:<31}##\n"
            "##  in the environment / .env and restart fake-wdp, or    ##\n"
            "##  the Query-3B demo denial will not happen.             ##\n"
            "############################################################\n"
        )

    gaps = plan_gaps(orcids, deny, random.Random(RNG_SEED))
    errors = verify_deny_orcids(deny, orcids, gaps)
    if errors:
        for error in errors:
            print(f"deny-orcid verification failed: {error}", file=sys.stderr)
        sys.exit(1)

    gen = generate(persons, entities, gaps, random.Random(RNG_SEED))
    persons_by_orcid = {p.orcid: p.name for p in persons}
    anchors = select_anchors(pairs, persons_by_orcid, gaps, deny)
    load_counts = asyncio.run(load(args.dsn, gen, anchors))
    print(render_report(gen, gaps, anchors, load_counts))


if __name__ == "__main__":
    main()
