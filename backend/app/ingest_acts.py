"""Ingest the contract/commercial acts the drafting agent needs.

The drafting types that came back with ZERO citable sources - cheque bounce
notices, NDAs, employment agreements, rent and eviction notices - all depend
on acts that aren't in bns.json or coi.json:

    Negotiable Instruments Act 1881   s.138  cheque dishonour
    Indian Contract Act 1872          s.27   restraint of trade
    Transfer of Property Act 1882     s.106  notice to quit

This pulls what's publicly available. NI Act comes from the civictech repo
(same source as the earlier bare acts). Contract Act and TP Act have no clean
public JSON I could verify, so a minimal set of the sections the drafting
agent actually cites is included inline. Replace with a full parse when you
have one - statutes.py picks up whatever is in the file.

    python -m app.ingest_acts
"""

import json
import sys
from pathlib import Path

import httpx

BASE = "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

REMOTE = {"nia.json": "nia.json"}

# Sections the drafting agent needs but that have no verified public JSON.
# Text abridged to the operative parts - enough to ground a citation.
CONTRACT_ACT = [
    {"chapter_title": "Void agreements", "Section": 27,
     "section_title": "Agreement in restraint of trade, void",
     "section_desc": (
         "Every agreement by which any one is restrained from exercising a "
         "lawful profession, trade or business of any kind, is to that extent "
         "void. Exception 1: One who sells the goodwill of a business may "
         "agree with the buyer to refrain from carrying on a similar business "
         "within specified local limits, so long as the buyer carries on a "
         "like business therein, provided such limits appear to the Court "
         "reasonable."
     )},
    {"chapter_title": "Void agreements", "Section": 28,
     "section_title": "Agreements in restraint of legal proceedings, void",
     "section_desc": (
         "Every agreement by which any party is restricted absolutely from "
         "enforcing his rights under or in respect of any contract by the "
         "usual legal proceedings in the ordinary tribunals, or which limits "
         "the time within which he may thus enforce his rights, is void to "
         "that extent."
     )},
    {"chapter_title": "Void agreements", "Section": 23,
     "section_title": "What considerations and objects are lawful",
     "section_desc": (
         "The consideration or object of an agreement is lawful unless it is "
         "forbidden by law; or is of such a nature that, if permitted, it "
         "would defeat the provisions of any law; or is fraudulent; or "
         "involves or implies injury to the person or property of another; or "
         "the Court regards it as immoral or opposed to public policy. In each "
         "of these cases the agreement is void."
     )},
    {"chapter_title": "Contracts", "Section": 73,
     "section_title": "Compensation for loss or damage caused by breach of contract",
     "section_desc": (
         "When a contract has been broken, the party who suffers is entitled "
         "to receive from the party who has broken it compensation for any "
         "loss or damage caused which naturally arose in the usual course of "
         "things from such breach, or which the parties knew when they made "
         "the contract to be likely to result from its breach. Such "
         "compensation is not to be given for any remote and indirect loss."
     )},
    {"chapter_title": "Contracts", "Section": 74,
     "section_title": "Compensation for breach where penalty stipulated for",
     "section_desc": (
         "When a contract has been broken, if a sum is named in the contract "
         "as the amount to be paid in case of such breach, the party "
         "complaining of the breach is entitled to receive from the party who "
         "has broken it reasonable compensation not exceeding the amount so "
         "named, whether or not actual damage or loss is proved to have been "
         "caused."
     )},
]

TP_ACT = [
    {"chapter_title": "Leases of immoveable property", "Section": 106,
     "section_title": "Duration of certain leases in absence of written contract or local usage",
     "section_desc": (
         "In the absence of a contract or local law or usage to the contrary, "
         "a lease of immoveable property for agricultural or manufacturing "
         "purposes shall be deemed to be a lease from year to year, "
         "terminable by six months' notice; and a lease of immoveable "
         "property for any other purpose shall be deemed to be a lease from "
         "month to month, terminable by fifteen days' notice. Every notice "
         "must be in writing, signed by or on behalf of the person giving it, "
         "and either be sent by post to the party intended to be bound by it "
         "or be tendered or delivered personally to such party."
     )},
    {"chapter_title": "Leases of immoveable property", "Section": 105,
     "section_title": "Lease defined",
     "section_desc": (
         "A lease of immoveable property is a transfer of a right to enjoy "
         "such property, made for a certain time, express or implied, or in "
         "perpetuity, in consideration of a price paid or promised, or of "
         "money, a share of crops, service or any other thing of value, to be "
         "rendered periodically or on specified occasions to the transferor "
         "by the transferee, who accepts the transfer on such terms."
     )},
    {"chapter_title": "Leases of immoveable property", "Section": 108,
     "section_title": "Rights and liabilities of lessor and lessee",
     "section_desc": (
         "In the absence of a contract or local usage to the contrary, the "
         "lessor and the lessee of immoveable property possess the rights and "
         "are subject to the liabilities mentioned in the rules below. The "
         "lessee is bound to pay the premium or rent at the proper time and "
         "place, to keep the property in as good condition as it was when he "
         "was put in possession, and on determination of the lease to put the "
         "lessor into possession of the property."
     )},
    {"chapter_title": "Leases of immoveable property", "Section": 111,
     "section_title": "Determination of lease",
     "section_desc": (
         "A lease of immoveable property determines by efflux of the time "
         "limited thereby; where such time is limited conditionally on the "
         "happening of some event, by the happening of such event; by express "
         "surrender; by forfeiture; or on the expiration of a notice to "
         "determine the lease, or to quit, duly given by one party to the "
         "other."
     )},
]


RTI_ACT = [
    {"chapter_title": "Right to information", "Section": 3,
     "section_title": "Right to information",
     "section_desc": (
         "Subject to the provisions of this Act, all citizens shall have the "
         "right to information."
     )},
    {"chapter_title": "Right to information", "Section": 6,
     "section_title": "Request for obtaining information",
     "section_desc": (
         "A person, who desires to obtain any information under this Act, "
         "shall make a request in writing or through electronic means in "
         "English or Hindi or in the official language of the area in which "
         "the application is being made, accompanying such fee as may be "
         "prescribed, to the Central Public Information Officer or State "
         "Public Information Officer of the concerned public authority, "
         "specifying the particulars of the information sought by him. An "
         "applicant making request for information shall not be required to "
         "give any reason for requesting the information or any other personal "
         "details except those that may be necessary for contacting him."
     )},
    {"chapter_title": "Right to information", "Section": 7,
     "section_title": "Disposal of request",
     "section_desc": (
         "The Public Information Officer shall, as expeditiously as possible, "
         "and in any case within thirty days of the receipt of the request, "
         "either provide the information on payment of such fee as may be "
         "prescribed or reject the request for any of the reasons specified in "
         "sections 8 and 9. Where the information sought for concerns the life "
         "or liberty of a person, the same shall be provided within forty-eight "
         "hours of the receipt of the request. If the Public Information Officer "
         "fails to give decision on the request within the period specified, he "
         "shall be deemed to have refused the request."
     )},
    {"chapter_title": "Right to information", "Section": 8,
     "section_title": "Exemption from disclosure of information",
     "section_desc": (
         "There shall be no obligation to give any citizen information the "
         "disclosure of which would prejudicially affect the sovereignty and "
         "integrity of India; information which has been expressly forbidden to "
         "be published by any court; information the disclosure of which would "
         "cause a breach of privilege of Parliament or the State Legislature; "
         "information including commercial confidence, trade secrets or "
         "intellectual property; information which relates to personal "
         "information the disclosure of which has no relationship to any public "
         "activity or interest. Provided that information which cannot be denied "
         "to Parliament or a State Legislature shall not be denied to any person."
     )},
    {"chapter_title": "Right to information", "Section": 19,
     "section_title": "Appeal",
     "section_desc": (
         "Any person who does not receive a decision within the time specified "
         "or is aggrieved by a decision of the Public Information Officer, may "
         "within thirty days from the expiry of such period or from the receipt "
         "of such decision prefer an appeal to such officer who is senior in "
         "rank to the Public Information Officer in each public authority. A "
         "second appeal against the decision shall lie within ninety days to "
         "the Central Information Commission or the State Information "
         "Commission."
     )},
]

LOCAL = {"contract.json": CONTRACT_ACT, "tpa.json": TP_ACT, "rti.json": RTI_ACT}


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    total = 0

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for dest, name in REMOTE.items():
            try:
                resp = client.get(f"{BASE}/{name}")
                resp.raise_for_status()
                rows = resp.json()
            except Exception as exc:
                print(f"  FAILED  {dest}: {exc}")
                continue
            (DATA_DIR / dest).write_text(
                json.dumps(rows, ensure_ascii=False), encoding="utf-8"
            )
            count = len(rows) if isinstance(rows, list) else 0
            total += count
            print(f"  ok      {dest:14s} {count:4d} sections  (downloaded)")

    for dest, rows in LOCAL.items():
        (DATA_DIR / dest).write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        total += len(rows)
        print(f"  ok      {dest:14s} {len(rows):4d} sections  (bundled subset)")

    print(f"\n{total} sections written to {DATA_DIR}")
    print("Restart the API so the statute index reloads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())