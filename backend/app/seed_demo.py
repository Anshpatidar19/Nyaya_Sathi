"""Seed (and un-seed) demo accounts for the advocate network.

    python -m app.seed_demo            # create / update the demo accounts
    python -m app.seed_demo --list     # print the login credentials
    python -m app.seed_demo --reset    # delete every demo account, nothing else

WHY THIS DOES NOT USE /auth/register
------------------------------------
Real signups go through Supabase's /signup endpoint and stay behind email
confirmation - that flow is untouched by this script and must stay that way.
Demo accounts use the ADMIN endpoint instead (`admin_create_user`, which
posts to /auth/v1/admin/users with email_confirm=true), so:

  * no verification email is ever sent to a fake address, and
  * only these accounts are created pre-confirmed.

The service_role key never leaves the backend, and this script is a CLI you
run yourself - there is no HTTP route that can create a confirmed account.

WHAT MAKES THEM SAFE TO DELETE
------------------------------
Three independent markers:

  1. `users.is_demo = true` - every delete below filters on this, so the
     reset path cannot reach a real account even if run carelessly.
  2. Emails are at example.com, which is IANA-reserved and undeliverable.
  3. One shared password from settings.demo_password.

Idempotent: run it as often as you like. An account that already exists is
updated in place rather than duplicated.
"""

import asyncio
import sys
from typing import Dict, List, Optional

import httpx
from sqlalchemy import or_

from . import models, supabase_auth
from .auth import ensure_profile
from .config import settings
from .database import SessionLocal

TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------
# Fictional people. Every name, firm, bar council number and college
# attribution below is invented for testing and refers to no real advocate.
# Bar numbers follow the shape of a real enrolment number but are not valid.
#
# City spread is deliberately Madhya Pradesh-heavy: 9 of the 34 advocates
# (26%) are in MP and 6 of those are in Indore, so a search for "Indore"
# returns a full page of results rather than one lonely card. Experience
# runs from 2 to 29 years across 15 specializations, so every filter on the
# search page has something to actually filter.

ADVOCATES: List[dict] = [
    # --- Indore (5) --------------------------------------------------------
    dict(
        first="Ankit", last="Verma", city="Indore", state="Madhya Pradesh",
        specialization="Criminal Law", years=14,
        practice_areas="Criminal Law, Bail Matters, Cheque Bounce, NDPS",
        courts="District Court Indore, MP High Court (Indore Bench)",
        languages="Hindi, English",
        firm="Verma & Associates",
        llb_college="Devi Ahilya Vishwavidyalaya, Indore", llb_year=2010,
        bar="MP/2841/2010",
        bio="Trial court criminal practice in Indore with a focus on bail and "
            "sessions matters. Regularly appears in NDPS and cheque dishonour "
            "cases before the District Court and the Indore Bench.",
        notable="Handles sessions trials and bail applications as lead counsel.",
    ),
    dict(
        first="Shruti", last="Deshmukh", city="Indore", state="Madhya Pradesh",
        specialization="Family Law", years=9,
        practice_areas="Family Law, Divorce, Maintenance, Child Custody, Domestic Violence",
        courts="Family Court Indore, District Court Indore",
        languages="Hindi, English, Marathi",
        firm="Deshmukh Legal Chambers",
        llb_college="Indore Institute of Law", llb_year=2015,
        bar="MP/5112/2015",
        bio="Family court practice covering divorce, maintenance and custody. "
            "Works extensively on mediation before contested proceedings and on "
            "protection orders under the Domestic Violence Act.",
        notable="Mediation-first approach in contested matrimonial matters.",
    ),
    dict(
        first="Rajat", last="Malviya", city="Indore", state="Madhya Pradesh",
        specialization="Property & Civil Law", years=22,
        practice_areas="Property Disputes, Partition Suits, Title Verification, Civil Law",
        courts="District Court Indore, MP High Court (Indore Bench)",
        languages="Hindi, English",
        firm="Malviya Law Office",
        llb_college="Devi Ahilya Vishwavidyalaya, Indore", llb_year=2002,
        llm="Devi Ahilya Vishwavidyalaya, Indore",
        bar="MP/1147/2002",
        bio="Two decades of civil and property litigation in Indore. Ancestral "
            "property partition, title disputes and specific performance suits, "
            "including long-running family property matters.",
        notable="Partition and title litigation across Malwa region districts.",
    ),
    dict(
        first="Neha", last="Jain", city="Indore", state="Madhya Pradesh",
        specialization="Corporate & Commercial Law", years=7,
        practice_areas="Corporate Law, Contracts, Startup Advisory, Company Compliance",
        courts="MP High Court (Indore Bench), NCLT Indore",
        languages="Hindi, English",
        firm="Jain & Kothari Advisors",
        llb_college="National Law Institute University, Bhopal", llb_year=2017,
        bar="MP/6403/2017",
        bio="Commercial advisory for small businesses and early-stage companies "
            "in Indore - shareholder agreements, vendor contracts and ROC "
            "compliance, with insolvency work before the NCLT.",
        notable="Advisory retainers for manufacturing SMEs in Pithampur.",
    ),
    dict(
        first="Imran", last="Qureshi", city="Indore", state="Madhya Pradesh",
        specialization="Motor Accident Claims", years=11,
        practice_areas="Motor Accident Claims, Insurance Disputes, Consumer Law",
        courts="MACT Indore, District Court Indore",
        languages="Hindi, English, Urdu",
        firm="Qureshi Associates",
        llb_college="Shri Vaishnav Institute of Law, Indore", llb_year=2013,
        bar="MP/4290/2013",
        bio="Compensation claims before the Motor Accident Claims Tribunal and "
            "insurance repudiation disputes. Also appears in consumer forums "
            "against insurers for delayed and rejected claims.",
        notable="Volume MACT practice with an emphasis on quantum arguments.",
    ),

    # --- Bhopal (2) --------------------------------------------------------
    dict(
        first="Priya", last="Sharma", city="Bhopal", state="Madhya Pradesh",
        specialization="Constitutional & Writ Practice", years=16,
        practice_areas="Constitutional Law, Writ Petitions, Service Matters, PIL",
        courts="MP High Court (Jabalpur), MP High Court (Bhopal Bench)",
        languages="Hindi, English",
        firm="Sharma Chambers",
        llb_college="National Law Institute University, Bhopal", llb_year=2008,
        llm="National Law Institute University, Bhopal",
        bar="MP/2016/2008",
        bio="Writ and service law practice before the MP High Court. Government "
            "service disputes, departmental enquiries and public interest "
            "matters relating to civic infrastructure.",
        notable="Service matters for state government employees.",
    ),
    dict(
        first="Vikram", last="Choudhary", city="Bhopal", state="Madhya Pradesh",
        specialization="Criminal Law", years=25,
        practice_areas="Criminal Law, White Collar Crime, Economic Offences, Appeals",
        courts="MP High Court (Bhopal Bench), District Court Bhopal",
        languages="Hindi, English",
        firm="Choudhary & Co.",
        llb_college="Barkatullah University, Bhopal", llb_year=1999,
        bar="MP/0884/1999",
        bio="Senior criminal practice in Bhopal, largely appellate. Economic "
            "offences, anticipatory bail and criminal revisions before the "
            "High Court.",
        notable="Appellate criminal work including economic offence appeals.",
    ),

    # --- Jabalpur (1) ------------------------------------------------------
    dict(
        first="Sunita", last="Patel", city="Jabalpur", state="Madhya Pradesh",
        specialization="Labour & Employment Law", years=13,
        practice_areas="Labour Law, Industrial Disputes, Wrongful Termination, ESI/PF",
        courts="MP High Court (Jabalpur), Labour Court Jabalpur",
        languages="Hindi, English",
        firm="Patel Legal",
        llb_college="Rani Durgavati Vishwavidyalaya, Jabalpur", llb_year=2011,
        bar="MP/3765/2011",
        bio="Labour and industrial disputes on both employee and management "
            "sides. Termination references, gratuity and provident fund "
            "recoveries, and conciliation before labour authorities.",
        notable="Industrial dispute references before the Labour Court.",
    ),

    # --- Delhi (3) ---------------------------------------------------------
    dict(
        first="Arjun", last="Mehra", city="New Delhi", state="Delhi",
        specialization="Corporate & Commercial Law", years=18,
        practice_areas="Mergers & Acquisitions, Corporate Law, Commercial Arbitration",
        courts="Delhi High Court, NCLT New Delhi, Supreme Court of India",
        languages="Hindi, English, Punjabi",
        firm="Mehra Partners",
        llb_college="Campus Law Centre, University of Delhi", llb_year=2006,
        llm="National Law School of India University, Bengaluru",
        bar="D/1402/2006",
        bio="Transactional and disputes practice - acquisitions, joint ventures "
            "and commercial arbitrations. Appears before the NCLT in oppression "
            "and mismanagement petitions.",
        notable="Advises on mid-market acquisitions and shareholder disputes.",
    ),
    dict(
        first="Fatima", last="Ansari", city="New Delhi", state="Delhi",
        specialization="Family Law", years=12,
        practice_areas="Family Law, Divorce, Maintenance, Guardianship, Muslim Personal Law",
        courts="Delhi High Court, Family Court Saket",
        languages="Hindi, English, Urdu",
        firm="Ansari Law Chambers",
        llb_college="Faculty of Law, Jamia Millia Islamia", llb_year=2012,
        bar="D/3318/2012",
        bio="Matrimonial and guardianship practice in the Delhi family courts, "
            "including cross-border custody questions and maintenance under "
            "both secular and personal law.",
        notable="Guardianship and custody matters involving NRI parties.",
    ),
    dict(
        first="Kabir", last="Rao", city="New Delhi", state="Delhi",
        specialization="Cyber & Technology Law", years=6,
        practice_areas="Cyber Law, Data Protection, Online Fraud, IT Act Matters",
        courts="Delhi High Court, District Court Patiala House",
        languages="English, Hindi",
        firm="Rao Tech Law",
        llb_college="National Law University, Delhi", llb_year=2018,
        bar="D/7241/2018",
        bio="Technology law practice covering data protection compliance, "
            "online financial fraud recovery and content takedowns under the "
            "IT Act and its intermediary rules.",
        notable="Online fraud recovery and platform takedown proceedings.",
    ),

    # --- Mumbai (3) --------------------------------------------------------
    dict(
        first="Meera", last="Iyer", city="Mumbai", state="Maharashtra",
        specialization="Real Estate & RERA", years=15,
        practice_areas="Real Estate, RERA Complaints, Redevelopment, Society Disputes",
        courts="Bombay High Court, MahaRERA, City Civil Court Mumbai",
        languages="English, Hindi, Marathi, Tamil",
        firm="Iyer & Bhatt",
        llb_college="Government Law College, Mumbai", llb_year=2009,
        bar="MH/2255/2009",
        bio="Real estate practice covering possession delays, redevelopment "
            "agreements and cooperative housing society disputes, with a "
            "substantial MahaRERA complaints docket.",
        notable="Homebuyer groups in delayed-possession RERA complaints.",
    ),
    dict(
        first="Rohan", last="Kulkarni", city="Mumbai", state="Maharashtra",
        specialization="Banking & Insolvency", years=20,
        practice_areas="Insolvency, Banking Law, DRT Matters, SARFAESI, Recovery",
        courts="Bombay High Court, NCLT Mumbai, DRT Mumbai",
        languages="English, Hindi, Marathi",
        firm="Kulkarni Legal LLP",
        llb_college="ILS Law College, Pune", llb_year=2004,
        llm="Government Law College, Mumbai",
        bar="MH/1330/2004",
        bio="Creditor-side insolvency and recovery practice - IBC petitions, "
            "SARFAESI enforcement and DRT proceedings for banks and asset "
            "reconstruction companies.",
        notable="Corporate insolvency resolution petitions before the NCLT.",
    ),
    dict(
        first="Ayesha", last="Merchant", city="Mumbai", state="Maharashtra",
        specialization="Intellectual Property", years=10,
        practice_areas="Trademarks, Copyright, Passing Off, Media & Entertainment Law",
        courts="Bombay High Court, IP Division",
        languages="English, Hindi, Gujarati",
        firm="Merchant IP Chambers",
        llb_college="Government Law College, Mumbai", llb_year=2014,
        bar="MH/4471/2014",
        bio="Trademark and copyright practice with a media and entertainment "
            "focus - brand enforcement, infringement injunctions and content "
            "licensing disputes.",
        notable="Interim injunctions in trademark passing-off actions.",
    ),

    # --- Bengaluru (2) -----------------------------------------------------
    dict(
        first="Nandini", last="Reddy", city="Bengaluru", state="Karnataka",
        specialization="Corporate & Commercial Law", years=8,
        practice_areas="Startup Advisory, Contracts, Employment Agreements, Fundraising",
        courts="Karnataka High Court, NCLT Bengaluru",
        languages="English, Kannada, Telugu, Hindi",
        firm="Reddy Counsel",
        llb_college="National Law School of India University, Bengaluru", llb_year=2016,
        bar="KAR/3902/2016",
        bio="Company-side commercial practice for technology startups - "
            "founder agreements, ESOP structuring, term sheets and commercial "
            "contract disputes.",
        notable="Advises early-stage companies through seed and Series A rounds.",
    ),
    dict(
        first="Suresh", last="Gowda", city="Bengaluru", state="Karnataka",
        specialization="Property & Civil Law", years=27,
        practice_areas="Property Disputes, Land Acquisition, Khata & Title Matters, Civil Law",
        courts="Karnataka High Court, City Civil Court Bengaluru",
        languages="Kannada, English, Hindi",
        firm="Gowda & Sons Advocates",
        llb_college="University Law College, Bangalore University", llb_year=1997,
        bar="KAR/0712/1997",
        bio="Long-standing civil practice in land and property matters - "
            "revenue records, title verification, land acquisition compensation "
            "and injunction suits.",
        notable="Land acquisition compensation references for landowners.",
    ),

    # --- Chennai (2) -------------------------------------------------------
    dict(
        first="Lakshmi", last="Narayanan", city="Chennai", state="Tamil Nadu",
        specialization="Tax Law", years=19,
        practice_areas="Income Tax, GST, Tax Appeals, Indirect Tax",
        courts="Madras High Court, ITAT Chennai, GST Appellate Authority",
        languages="Tamil, English, Hindi",
        firm="Narayanan Tax Chambers",
        llb_college="Dr. Ambedkar Government Law College, Chennai", llb_year=2005,
        llm="University of Madras",
        bar="TN/1861/2005",
        bio="Direct and indirect tax litigation - assessment appeals, GST "
            "demand notices and writ petitions against reassessment. Appears "
            "before the ITAT and the High Court.",
        notable="Tax appeals for manufacturing and export businesses.",
    ),
    dict(
        first="Deepak", last="Subramanian", city="Chennai", state="Tamil Nadu",
        specialization="Consumer Law", years=5,
        practice_areas="Consumer Law, Product Liability, Service Deficiency, Medical Negligence",
        courts="State Consumer Commission Tamil Nadu, District Consumer Forum Chennai",
        languages="Tamil, English",
        firm="Subramanian Legal",
        llb_college="School of Excellence in Law, Chennai", llb_year=2019,
        bar="TN/7710/2019",
        bio="Consumer forum practice against builders, insurers, hospitals and "
            "service providers. Handles deficiency of service and unfair trade "
            "practice complaints end to end.",
        notable="Consumer complaints against insurers and private hospitals.",
    ),

    # --- Hyderabad (2) -----------------------------------------------------
    dict(
        first="Kavya", last="Rao", city="Hyderabad", state="Telangana",
        specialization="Criminal Law", years=9,
        practice_areas="Criminal Law, Cheque Bounce, Bail, Women's Safety Matters",
        courts="Telangana High Court, District Court Hyderabad",
        languages="Telugu, English, Hindi",
        firm="Rao & Reddy Criminal Chambers",
        llb_college="NALSAR University of Law, Hyderabad", llb_year=2015,
        bar="TG/4088/2015",
        bio="Criminal trial practice in Hyderabad including bail applications, "
            "quashing petitions and Section 138 NI Act complaints. Also handles "
            "protection order proceedings.",
        notable="Quashing petitions before the Telangana High Court.",
    ),
    dict(
        first="Farhan", last="Siddiqui", city="Hyderabad", state="Telangana",
        specialization="Arbitration & Commercial Disputes", years=17,
        practice_areas="Arbitration, Commercial Suits, Construction Disputes, Contract Law",
        courts="Telangana High Court, Commercial Court Hyderabad",
        languages="English, Hindi, Urdu, Telugu",
        firm="Siddiqui Arbitration Practice",
        llb_college="Osmania University, Hyderabad", llb_year=2007,
        llm="NALSAR University of Law, Hyderabad",
        bar="TG/1993/2007",
        bio="Domestic arbitration and commercial litigation, with a construction "
            "and infrastructure focus - claims, delay analysis, Section 34 and "
            "Section 37 challenges.",
        notable="Construction claims arbitrations for contractors and employers.",
    ),

    # --- Kolkata (2) -------------------------------------------------------
    dict(
        first="Ananya", last="Banerjee", city="Kolkata", state="West Bengal",
        specialization="Family Law", years=14,
        practice_areas="Family Law, Divorce, Succession, Wills & Probate",
        courts="Calcutta High Court, City Civil Court Kolkata",
        languages="Bengali, English, Hindi",
        firm="Banerjee Chambers",
        llb_college="University of Calcutta", llb_year=2010,
        bar="WB/2604/2010",
        bio="Matrimonial and succession practice - contested divorce, "
            "testamentary matters, probate and letters of administration before "
            "the Calcutta High Court.",
        notable="Probate and succession certificate proceedings.",
    ),
    dict(
        first="Subir", last="Ghosh", city="Kolkata", state="West Bengal",
        specialization="Labour & Employment Law", years=23,
        practice_areas="Labour Law, Trade Union Matters, Retrenchment, Employment Contracts",
        courts="Calcutta High Court, Industrial Tribunal West Bengal",
        languages="Bengali, English, Hindi",
        firm="Ghosh & Dutta",
        llb_college="University of Calcutta", llb_year=2001,
        bar="WB/1058/2001",
        bio="Industrial relations practice representing unions and workmen in "
            "retrenchment, closure and wage settlement disputes before "
            "industrial tribunals.",
        notable="Closure and retrenchment references for workmen's unions.",
    ),

    # --- Pune (2) ----------------------------------------------------------
    dict(
        first="Aditi", last="Joshi", city="Pune", state="Maharashtra",
        specialization="Property & Civil Law", years=11,
        practice_areas="Property Disputes, Tenancy, Society Matters, Agreements to Sell",
        courts="Bombay High Court, District Court Pune",
        languages="Marathi, English, Hindi",
        firm="Joshi Legal Associates",
        llb_college="ILS Law College, Pune", llb_year=2013,
        bar="MH/3641/2013",
        bio="Civil and property practice in Pune - tenancy disputes, agreements "
            "to sell, society conveyance and title due diligence for resale "
            "purchases.",
        notable="Title due diligence and conveyance for housing societies.",
    ),
    dict(
        first="Nikhil", last="Pawar", city="Pune", state="Maharashtra",
        specialization="Motor Accident Claims", years=4,
        practice_areas="Motor Accident Claims, Insurance Law, Personal Injury",
        courts="MACT Pune, District Court Pune",
        languages="Marathi, Hindi, English",
        firm="Pawar Associates",
        llb_college="Symbiosis Law School, Pune", llb_year=2020,
        bar="MH/8203/2020",
        bio="Early-career practice focused on accident compensation claims and "
            "insurance disputes, including no-fault liability claims and "
            "third-party insurance matters.",
        notable="Personal injury compensation claims before the MACT.",
    ),

    # --- Jaipur, Lucknow, Ahmedabad, Chandigarh (4) ------------------------
    dict(
        first="Yashvi", last="Rathore", city="Jaipur", state="Rajasthan",
        specialization="Criminal Law", years=8,
        practice_areas="Criminal Law, Bail Matters, Domestic Violence, POCSO",
        courts="Rajasthan High Court (Jaipur Bench), District Court Jaipur",
        languages="Hindi, English",
        firm="Rathore Criminal Chambers",
        llb_college="University of Rajasthan, Jaipur", llb_year=2016,
        bar="RJ/3987/2016",
        bio="Criminal trial and bail practice in Jaipur, with significant work "
            "in offences against women and children, appearing for both "
            "complainants and the defence.",
        notable="POCSO and domestic violence trials at the district level.",
    ),
    dict(
        first="Alok", last="Tripathi", city="Lucknow", state="Uttar Pradesh",
        specialization="Constitutional & Writ Practice", years=21,
        practice_areas="Writ Petitions, Service Matters, Education Law, Constitutional Law",
        courts="Allahabad High Court (Lucknow Bench)",
        languages="Hindi, English",
        firm="Tripathi & Mishra",
        llb_college="University of Lucknow", llb_year=2003,
        llm="University of Lucknow",
        bar="UP/1276/2003",
        bio="Writ practice before the Lucknow Bench covering government service "
            "disputes, recruitment challenges and recognition matters for "
            "educational institutions.",
        notable="Recruitment and promotion challenges for state employees.",
    ),
    dict(
        first="Bhavna", last="Shah", city="Ahmedabad", state="Gujarat",
        specialization="Corporate & Commercial Law", years=13,
        practice_areas="Corporate Law, Contracts, Company Compliance, Commercial Disputes",
        courts="Gujarat High Court, NCLT Ahmedabad",
        languages="Gujarati, Hindi, English",
        firm="Shah Commercial Law",
        llb_college="Gujarat University, Ahmedabad", llb_year=2011,
        bar="GJ/3410/2011",
        bio="Company-side commercial practice for textile and chemical sector "
            "businesses - supply contracts, distributorship disputes and "
            "corporate compliance.",
        notable="Distributorship and supply contract disputes.",
    ),
    dict(
        first="Harpreet", last="Singh", city="Chandigarh", state="Chandigarh",
        specialization="Property & Civil Law", years=16,
        practice_areas="Property Disputes, Partition, Injunction Suits, Rent Matters",
        courts="Punjab & Haryana High Court, District Court Chandigarh",
        languages="Punjabi, Hindi, English",
        firm="Singh Law Chambers",
        llb_college="Panjab University, Chandigarh", llb_year=2008,
        bar="PB/2188/2008",
        bio="Civil practice in property and tenancy matters across Chandigarh "
            "and the tricity - partition suits, specific performance and rent "
            "control proceedings.",
        notable="Specific performance suits on agreements to sell.",
    ),

    # --- Nagpur, Patna, Kochi, Guwahati (4) --------------------------------
    dict(
        first="Sanjana", last="Deshpande", city="Nagpur", state="Maharashtra",
        specialization="Environmental Law", years=10,
        practice_areas="Environmental Law, NGT Matters, Mining Compliance, Public Interest",
        courts="National Green Tribunal (Central Zone), Bombay High Court (Nagpur Bench)",
        languages="Marathi, Hindi, English",
        firm="Deshpande Environmental Law",
        llb_college="Maharashtra National Law University, Nagpur", llb_year=2014,
        llm="TERI School of Advanced Studies",
        bar="MH/4522/2014",
        bio="Environmental practice before the NGT - air and water pollution "
            "complaints, environmental clearance challenges and mining "
            "compliance in the Vidarbha region.",
        notable="Pollution and clearance challenges before the NGT.",
    ),
    dict(
        first="Rakesh", last="Prasad", city="Patna", state="Bihar",
        specialization="Criminal Law", years=29,
        practice_areas="Criminal Law, Appeals, Sessions Trials, Land Dispute Criminal Matters",
        courts="Patna High Court, District Court Patna",
        languages="Hindi, English, Bhojpuri",
        firm="Prasad & Associates",
        llb_college="Patna University", llb_year=1995,
        bar="BR/0561/1995",
        bio="Nearly three decades of criminal practice in Patna, largely "
            "sessions trials and criminal appeals, including land dispute "
            "matters that turn criminal.",
        notable="Criminal appeals and revisions before the Patna High Court.",
    ),
    dict(
        first="Anjali", last="Menon", city="Kochi", state="Kerala",
        specialization="Consumer Law", years=12,
        practice_areas="Consumer Law, Medical Negligence, Banking Complaints, Service Deficiency",
        courts="Kerala High Court, State Consumer Commission Kerala",
        languages="Malayalam, English, Hindi",
        firm="Menon Consumer Law Practice",
        llb_college="Government Law College, Ernakulam", llb_year=2012,
        bar="KL/3221/2012",
        bio="Consumer and medical negligence practice in Kochi - hospital "
            "negligence claims, banking service complaints and insurance "
            "repudiations before the consumer commissions.",
        notable="Medical negligence claims before the State Commission.",
    ),
    dict(
        first="Bhaskar", last="Baruah", city="Guwahati", state="Assam",
        specialization="Constitutional & Writ Practice", years=18,
        practice_areas="Writ Petitions, Service Matters, Land Rights, Constitutional Law",
        courts="Gauhati High Court",
        languages="Assamese, Hindi, English, Bengali",
        firm="Baruah Chambers",
        llb_college="Gauhati University", llb_year=2006,
        bar="AS/1704/2006",
        bio="Writ practice before the Gauhati High Court covering service "
            "disputes, land and settlement rights, and administrative law "
            "challenges in the North East.",
        notable="Land settlement and service writ petitions.",
    ),

    # --- Two more, deliberately low-experience, to test the filter ---------
    dict(
        first="Ishita", last="Bhardwaj", city="Gurugram", state="Haryana",
        specialization="Cyber & Technology Law", years=3,
        practice_areas="Cyber Law, Online Fraud, Data Privacy, Consumer Tech Disputes",
        courts="District Court Gurugram, Punjab & Haryana High Court",
        languages="Hindi, English",
        firm="Independent practice",
        llb_college="Amity Law School, Noida", llb_year=2021,
        bar="HR/8801/2021",
        bio="Junior practice in cyber and consumer technology matters - UPI and "
            "online payment fraud recovery, privacy complaints and takedown "
            "requests.",
        notable="Digital payment fraud complaints and recovery.",
    ),
    dict(
        first="Manav", last="Kapoor", city="Indore", state="Madhya Pradesh",
        specialization="Tax Law", years=2,
        practice_areas="GST, Income Tax, Tax Compliance, Small Business Advisory",
        courts="MP High Court (Indore Bench), GST Appellate Authority Indore",
        languages="Hindi, English",
        firm="Independent practice",
        llb_college="Indore Institute of Law", llb_year=2022,
        bar="MP/8940/2022",
        bio="Junior tax practice in Indore assisting small traders and "
            "proprietorships with GST notices, registration disputes and "
            "routine compliance.",
        notable="GST notice replies and appeals for small traders.",
    ),
]


# Demo client accounts. Two, so you can test two people talking to the same
# advocate and confirm neither can see the other's conversation.
DEMO_USERS: List[dict] = [
    dict(
        first="Rahul", last="Sharma", city="Bhopal", state="Madhya Pradesh",
        bio="Demo client account. Testing a property dispute enquiry.",
    ),
    dict(
        first="Sneha", last="Patil", city="Indore", state="Madhya Pradesh",
        bio="Demo client account. Testing a family law enquiry.",
    ),
]


def _email(person: dict) -> str:
    slug = f"{person['first']}.{person['last']}".lower()
    return f"{slug}.demo@{settings.demo_email_domain}"


# ---------------------------------------------------------------------------
# Supabase Auth admin helpers
# ---------------------------------------------------------------------------
# `admin_create_user` already lives in supabase_auth. Lookup-by-email and
# delete do not, and they are only ever needed by this script, so they live
# here rather than widening the app's auth surface. Both reuse
# supabase_auth's header builder so the service key is read from one place.

async def _all_auth_users() -> Dict[str, str]:
    """email (lower) -> auth user id, for every user in the project.

    One listing rather than a lookup per account: 34 accounts would otherwise
    be 34 round trips before any work starts.
    """
    out: Dict[str, str] = {}
    page = 1
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while True:
            resp = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users",
                headers=supabase_auth._admin_headers(),
                params={"page": page, "per_page": 200},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Could not list auth users ({resp.status_code}): "
                    f"{resp.text[:200]}"
                )
            users = resp.json().get("users") or []
            if not users:
                break
            for u in users:
                if u.get("email"):
                    out[u["email"].lower()] = u["id"]
            if len(users) < 200:
                break
            page += 1
    return out


async def _delete_auth_user(auth_id: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.delete(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{auth_id}",
            headers=supabase_auth._admin_headers(),
        )
    if resp.status_code >= 400 and resp.status_code != 404:
        print(f"  ! could not delete auth user {auth_id}: {resp.status_code}")


async def _ensure_auth_account(
    person: dict, role: str, existing: Dict[str, str]
) -> Optional[str]:
    """Create the Supabase Auth user, or return the id of the existing one.

    Uses the ADMIN endpoint with email_confirm=true (see module docstring) so
    no verification email is sent to a fake address. Real signups still go
    through /signup and still require confirmation.
    """
    email = _email(person)
    if email.lower() in existing:
        return existing[email.lower()]

    name = f"{person['first']} {person['last']}"
    try:
        created = await supabase_auth.admin_create_user(
            email=email,
            password=settings.demo_password,
            metadata={
                "name": name,
                "state": person.get("state"),
                "role": role,
                "preferred_language": "en",
                # Read by nothing at runtime. It is here so the account is
                # obviously a test account in the Supabase dashboard too.
                "is_demo": True,
            },
        )
    except supabase_auth.AuthError as exc:
        print(f"  ! {email}: {exc}")
        return None

    return (created.get("user") or created).get("id")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

async def seed() -> None:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in backend/.env."
        )

    print("Reading existing auth users...")
    existing = await _all_auth_users()

    db = SessionLocal()
    created_adv = updated_adv = created_usr = updated_usr = 0
    try:
        for person in ADVOCATES:
            auth_id = await _ensure_auth_account(person, "advocate", existing)
            if not auth_id:
                continue

            email = _email(person)
            name = f"{person['first']} {person['last']}"

            user = ensure_profile(
                db,
                auth_id=auth_id,
                email=email,
                name=name,
                state=person.get("state"),
                preferred_language="en",
                role="advocate",
            )
            # ensure_profile never overwrites an existing role, and these
            # accounts are ours, so set the rest explicitly.
            fresh = user.city is None
            user.name = name
            user.role = "advocate"
            user.city = person["city"]
            user.state = person["state"]
            user.bio = person["bio"]
            user.is_demo = True

            profile = (
                db.query(models.AdvocateProfile)
                .filter(models.AdvocateProfile.user_id == user.id)
                .first()
            )
            if not profile:
                profile = models.AdvocateProfile(user_id=user.id)
                db.add(profile)

            profile.bar_council_number = person["bar"]
            profile.years_experience = person["years"]
            profile.professional_bio = person["bio"]
            profile.current_firm = person["firm"]
            profile.practice_city = person.get("practice_city") or person["city"]
            profile.specialization = person["specialization"]
            profile.practice_areas = person["practice_areas"]
            profile.courts = person["courts"]
            profile.languages = person["languages"]
            profile.llb_college = person["llb_college"]
            profile.llb_year = person["llb_year"]
            profile.llm_college = person.get("llm")
            profile.notable_experience = person.get("notable")
            profile.is_listed = True

            db.commit()
            if fresh:
                created_adv += 1
                print(f"  + advocate  {name:<22} {person['city']:<12} {email}")
            else:
                updated_adv += 1

        for person in DEMO_USERS:
            auth_id = await _ensure_auth_account(person, "user", existing)
            if not auth_id:
                continue

            email = _email(person)
            name = f"{person['first']} {person['last']}"

            user = ensure_profile(
                db,
                auth_id=auth_id,
                email=email,
                name=name,
                state=person.get("state"),
                preferred_language="en",
                role="user",
            )
            fresh = user.city is None
            user.name = name
            user.role = "user"
            user.city = person["city"]
            user.state = person["state"]
            user.bio = person["bio"]
            user.is_demo = True
            db.commit()

            if fresh:
                created_usr += 1
                print(f"  + client    {name:<22} {person['city']:<12} {email}")
            else:
                updated_usr += 1
    finally:
        db.close()

    print()
    print(f"Advocates: {created_adv} created, {updated_adv} updated")
    print(f"Clients:   {created_usr} created, {updated_usr} updated")
    print(f"Password for every demo account: {settings.demo_password}")
    print()
    print("Run `python -m app.seed_demo --list` for the full credential list.")


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

async def reset() -> None:
    """Delete every demo account and everything hanging off it.

    Every delete filters on users.is_demo, so this cannot touch a real
    account. Rows are removed child-first rather than relying on ON DELETE
    CASCADE, so the script behaves identically on SQLite (where foreign keys
    may be off) and on Postgres.
    """
    db = SessionLocal()
    try:
        demo = db.query(models.User).filter(models.User.is_demo.is_(True)).all()
        if not demo:
            print("No demo accounts found. Nothing to do.")
            return

        ids = [u.id for u in demo]
        auth_ids = [u.auth_id for u in demo if u.auth_id]
        print(f"Removing {len(ids)} demo accounts...")

        # Threads the demo accounts participate in - their messages belong to
        # the real user on the other side too, and go with the thread.
        thread_ids = [
            r[0]
            for r in db.query(models.ChatParticipant.thread_id)
            .filter(models.ChatParticipant.user_id.in_(ids))
            .distinct()
            .all()
        ]
        conn_ids = [
            r[0]
            for r in db.query(models.Connection.id)
            .filter(
                models.Connection.requester_id.in_(ids)
                | models.Connection.receiver_id.in_(ids)
            )
            .all()
        ]

        # Message ids in those threads, resolved to a plain list. Passing a
        # subquery to .in_() here worked by accident on some SQLAlchemy
        # versions and not others; a list of ints always works, and at demo
        # scale it is a handful of rows.
        message_ids = (
            [
                r[0]
                for r in db.query(models.ChatMessage.id)
                .filter(models.ChatMessage.thread_id.in_(thread_ids))
                .all()
            ]
            if thread_ids
            else []
        )

        # Notifications first, including ones sent TO a real user ABOUT a
        # demo account - those would otherwise linger as a dangling badge
        # pointing at an account that no longer exists.
        #
        # Conditions are collected in a list and OR-ed with or_(*conds).
        # Writing this as `cond_a | (cond_b if ids else False)` looks
        # equivalent but is not: a bare Python False in a SQLAlchemy boolean
        # expression is not a valid clause.
        note_conds = [
            models.Notification.recipient_id.in_(ids),
            models.Notification.related_user_id.in_(ids),
        ]
        if thread_ids:
            note_conds.append(models.Notification.related_thread_id.in_(thread_ids))
        if conn_ids:
            note_conds.append(
                models.Notification.related_connection_id.in_(conn_ids)
            )
        db.query(models.Notification).filter(or_(*note_conds)).delete(
            synchronize_session=False
        )

        if message_ids:
            # Detach chat attachments before the messages go, so a real
            # user's uploaded document survives - only the link to the
            # deleted message is dropped.
            db.query(models.Document).filter(
                models.Document.message_id.in_(message_ids)
            ).update({"message_id": None}, synchronize_session=False)

            db.query(models.ChatMessage).filter(
                models.ChatMessage.id.in_(message_ids)
            ).delete(synchronize_session=False)

        if thread_ids:
            db.query(models.ChatParticipant).filter(
                models.ChatParticipant.thread_id.in_(thread_ids)
            ).delete(synchronize_session=False)
            db.query(models.ChatThread).filter(
                models.ChatThread.id.in_(thread_ids)
            ).delete(synchronize_session=False)

        if conn_ids:
            db.query(models.Connection).filter(
                models.Connection.id.in_(conn_ids)
            ).delete(synchronize_session=False)

        db.query(models.AdvocateProfile).filter(
            models.AdvocateProfile.user_id.in_(ids)
        ).delete(synchronize_session=False)

        # Anything a demo account did in the AI half of the app.
        log_ids = [
            r[0]
            for r in db.query(models.QueryLog.id)
            .filter(models.QueryLog.user_id.in_(ids))
            .all()
        ]
        if log_ids:
            db.query(models.AnswerTranslation).filter(
                models.AnswerTranslation.query_log_id.in_(log_ids)
            ).delete(synchronize_session=False)
            db.query(models.QueryLog).filter(
                models.QueryLog.id.in_(log_ids)
            ).delete(synchronize_session=False)
        db.query(models.Conversation).filter(
            models.Conversation.user_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(models.Document).filter(
            models.Document.user_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(models.MatterNote).filter(
            models.MatterNote.user_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(models.MatterEvent).filter(
            models.MatterEvent.user_id.in_(ids)
        ).delete(synchronize_session=False)
        db.query(models.Matter).filter(
            models.Matter.user_id.in_(ids)
        ).delete(synchronize_session=False)

        db.query(models.User).filter(
            models.User.id.in_(ids), models.User.is_demo.is_(True)
        ).delete(synchronize_session=False)

        db.commit()
        print(f"  - {len(ids)} rows removed from public.users")
    finally:
        db.close()

    print("Removing Supabase Auth accounts...")
    for auth_id in auth_ids:
        await _delete_auth_user(auth_id)
    print(f"  - {len(auth_ids)} auth accounts removed")
    print("Done. No real account was touched.")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_credentials() -> None:
    print()
    print("=" * 78)
    print("DEMO LOGIN CREDENTIALS")
    print(f"Password for every account below: {settings.demo_password}")
    print("=" * 78)
    print()
    print("CLIENTS")
    for p in DEMO_USERS:
        print(f"  {p['first'] + ' ' + p['last']:<22} {p['city']:<12} {_email(p)}")
    print()
    print("ADVOCATES")
    for p in sorted(ADVOCATES, key=lambda x: (x["state"], x["city"], x["last"])):
        label = f"{p['first']} {p['last']}"
        print(
            f"  {label:<22} {p['city']:<12} {p['specialization']:<32} "
            f"{p['years']:>2}y  {_email(p)}"
        )
    print()
    print("Suggested test run:")
    print(f"  1. Log in as {_email(DEMO_USERS[0])}")
    print("  2. Find an Advocate -> search 'Criminal' -> city 'Indore'")
    print("  3. Open Ankit Verma's profile -> Connect")
    print(f"  4. Incognito window, log in as {_email(ADVOCATES[0])}")
    print("  5. Accept the request, then chat from both windows")
    print()


def main() -> None:
    args = set(sys.argv[1:])
    if "--list" in args:
        list_credentials()
    elif "--reset" in args:
        asyncio.run(reset())
    else:
        asyncio.run(seed())
        list_credentials()


if __name__ == "__main__":
    main()