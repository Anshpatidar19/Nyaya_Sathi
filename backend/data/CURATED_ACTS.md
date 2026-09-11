# Curated act list — corpus extension

Source: `mratanusarkar/Indian-Laws` (Hugging Face).
Registry: `backend/app/statutes_ext.py`. Act-level status: `backend/data/repeal_map.json` → `_acts`.

Selection rule: an explicit allowlist, not a percentage of the dataset.
Everything not listed here is excluded by default.

## Included

| act_key | Act | short | year | status | tier |
|---|---|---|---|---|---|
| `bnss` | Bharatiya Nagarik Suraksha Sanhita, 2023 | BNSS | 2023 | successor | priority |
| `bsa` | Bharatiya Sakshya Adhiniyam, 2023 | BSA | 2023 | successor | priority |
| `dowry` | Dowry Prohibition Act, 1961 | Dowry Prohibition Act | 1961 | live | priority |
| `hsa` | Hindu Succession Act, 1956 | HSA | 1956 | live | priority |
| `isa` | Indian Succession Act, 1925 | Indian Succession Act | 1925 | live | priority |
| `pocso` | Protection of Children from Sexual Offences Act, 2012 | POCSO | 2012 | live | priority |
| `pwdva` | Protection of Women from Domestic Violence Act, 2005 | DV Act | 2005 | live | priority |
| `scst` | Scheduled Castes and the Scheduled Tribes (Prevention of Atrocities) Act, 1989 | SC/ST Act | 1989 | live | priority |
| `guardians_wards` | Guardians and Wards Act, 1890 | Guardians and Wards Act | 1890 | live | core |
| `hama` | Hindu Adoptions and Maintenance Act, 1956 | HAMA | 1956 | live | core |
| `hmga` | Hindu Minority and Guardianship Act, 1956 | HMGA | 1956 | live | core |
| `stamp` | Indian Stamp Act, 1899 | Stamp Act | 1899 | live | core |
| `jj` | Juvenile Justice (Care and Protection of Children) Act, 2015 | JJ Act | 2015 | live | core |
| `senior_citizens` | Maintenance and Welfare of Parents and Senior Citizens Act, 2007 | Senior Citizens Act | 2007 | live | core |
| `ndps` | Narcotic Drugs and Psychotropic Substances Act, 1985 | NDPS Act | 1985 | live | core |
| `pca` | Prevention of Corruption Act, 1988 | PC Act | 1988 | live | core |
| `phra` | Protection of Human Rights Act, 1993 | PHR Act | 1993 | live | core |
| `registration` | Registration Act, 1908 | Registration Act | 1908 | live | core |
| `rpwd` | Rights of Persons with Disabilities Act, 2016 | RPwD Act | 2016 | live | core |
| `posh` | Sexual Harassment of Women at Workplace (Prevention, Prohibition and Redressal) Act, 2013 | POSH Act | 2013 | live | core |
| `sma` | Special Marriage Act, 1954 | Special Marriage Act | 1954 | live | core |
| `companies` | Companies Act, 2013 | Companies Act | 2013 | live | optional |
| `easements` | Indian Easements Act, 1882 | Easements Act | 1882 | live | optional |
| `partnership` | Indian Partnership Act, 1932 | Partnership Act | 1932 | live | optional |

## Excluded by rule, not by omission

| Rule | What it drops |
|---|---|
| `AMENDING_RE` | Amendment Acts, Repealing and Amending Acts, Adaptation of Laws Acts. An amendment act is a diff, not a law — it parses cleanly and reads like law, which is exactly why it is dangerous. The consolidated SC/ST Act, 1989 is ingested; its 2015 amendment act is not. |
| `NON_PRIMARY_RE` | Rules, Regulations, Schemes, Notifications, Orders, Bye-laws, Ordinances, Bills. Real law, wrong corpus — this index answers "what does the Act say". |
| `hf_reject` per act | Superseded editions of an allowlisted act (JJ Act 2000 vs 2015, Companies Act 1956 vs 2013, PC Act 1947 vs 1988) and near-miss titles (Societies Registration Act vs Registration Act). |
| already in local corpus | Any `act_key:section` already served by `statutes._index()`. Never overwritten unless `--replace`. |
| content hash | Same act + section + normalised body seen twice under different `act_title` spellings. |
| stub filter | Bodies under 60 chars, or beginning `[Repealed]` / `Omitted` / `Deleted`. |

## Status vocabulary

| Status | Meaning |
|---|---|
| `live` | In force, consolidated text. |
| `repealed` | No longer in force. `successor_act_key` names what replaced it. |
| `amending` | An amendment act. Never ingested; the value exists so the filter can name what it dropped. |
| `successor` | In force, and it is what replaced a repealed act. Lets the app say "CrPC 154 is now BNSS 173" rather than only "CrPC is repealed". |

Act-level mappings now in `repeal_map.json._acts`:

    ipc  → bns    (repealed → successor)
    crpc → bnss   (repealed → successor)
    iea  → bsa    (repealed → successor)