"""
This module provides a parser for Google case law
pages available at https://scholar.google.com/.

The offered features include scraping, parsing, serializing
and tagging important data such as bluebook citations, judge names, courts,
decision dates, case numbers, patents in suit, cited claims, footnotes and etc.

It also provides a useful labeled text of the case file that can be utilized
in machine-learning applications.
"""

from __future__ import absolute_import

import json
import re
import sys
from datetime import datetime
from functools import reduce
from operator import concat
from pathlib import Path
from tqdm import tqdm
import requests
from bs4 import BeautifulSoup as BS

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, BASE_DIR.__str__())

from tools.utils import (
    rm_tree,
    closest_value,
    multiprocess,
    create_dir,
    deaccent,
    hyphen_to_numbers,
    regex,
    remove_repeated,
    sort_int,
    validate_url,
    switch_ip,
    proxy_browser,
    recaptcha_process,
    async_get,
)

__author__ = {"github.com/": ["altabeh"]}
__all__ = ["GCLParse"]


class GCLParse(object):
    """
    Parser for Google case law pages.
    """

    base_url = "https://scholar.google.com/"

    # ------ Regex Patterns ------
    case_patterns = [(r"/scholar_case?(?:.*?)=(\d+)", r"\g<1>")]
    casenumber_patterns = [(r"scidkt=(.*?)&", "")]
    just_number_patterns = [(r"^\d+$", "")]
    docket_patterns = [
        (r"(?:[\w .]+)?(?:C\.A|N[Oo][sS]?)\.:? ?", ""),
        (r"\([\w ]+\)", ""),
        (r",? +[Aa][Nn][Dd] +", ","),
    ]
    docket_appeals_patterns = [(r"(?:\d{2,4}|(?<=, )|(?<=, and)(?: +)?)-\d{1,5}", "")]
    docket_us_patterns = [(r"\d+(?:-\d+)?", "")]
    patent_number_pattern = r"(?:(?:RE|PP|D|AI|X|H|T)?\d{1,2}[,./]\-?)?(?:(?:RE|PP|D|AI|X|H|T)\d{2,3}|\d{3})[,./]\-?\d{3}(?: ?AI)?\b"
    patent_reference_pattern = r'["`\'#]+(\d{3}) ?(?:[Aa]pplication|[Pp]atent)\b'
    claim_patterns_1 = r"([Cc]laims?([\d\-, and]+)(?:[\w ]+)(?:(?:[\(\"“ ]+)?(?: ?the ?)?[#`\']+(\d+)))"
    claim_patterns_2 = r"(?<=[cC]laim[s ])(?:([\d,\- ]+)(?:(?:[, ]+)?and ([\d\- ]+))*)+"
    patent_number_patterns_1 = [(r" " + patent_number_pattern, "")]
    patent_number_patterns_2 = [(r"[USnitedpPaNso. ]+" + patent_number_pattern, "")]
    federal_court_patterns = [(r"( ?([,-]) ([\w:. \']+) (\d{4}))$", "")]
    state_court_patterns = [(r"( ?([-,]) ([\w. ]+): (.*?) (\d{4}))$", "")]
    judge_patterns = [
        (
            r"^(m[rs]s?\.? )?C[Hh][Ii][Ee][Ff] J[Uu][Dd][Gg][Ee][Ss]? |^(m[rs]s?\.? )?(?:C[Hh][Ii][Ee][Ff] )?J[Uu][Ss][Tt][Ii][Cc][Ee][Ss]? |^P[rR][Ee][Ss][Ee][nN][T]: |^B[eE][fF][oO][rR][Ee]: | J[Uu][Dd][Gg][Ee][Ss]?[:.]?$|, [UJSC. ]+:?$|, (?:[USD. ]+)?[J. ]+:?$|, J[Uu][Ss][Tt][Ii][Cc][Ee][Ss]?\.?$",
            "",
        )
    ]
    judge_clean_patterns_1 = [
        (
            r", joined$| ?—$| ?@@@@\[[\d\*]+\] ?$|^Opinion of the Court by |, United States District Court| ?Pending before the Court are:?| ?Opinion for the court filed by[\w\'., ]+| delivered the opinion of the Court\.|^Appeal from ",
            "",
        )
    ]
    judge_clean_patterns_2 = [
        (
            r"^(?:the )?hon\. |^(?:the )?honorable |^before:? |^present:? |^m[rs]s?\.? |, (?:u\.?\s\.?)?d?\.?j\.\.?$|, j\.s\.c\.$",
            "",
        )
    ]
    judge_clean_patterns_3 = [
        (
            r"senior|chief|u\.?s\.?|united states|circuit|district|magistrate|chief|court|judges?",
            "",
        )
    ]
    date_patterns = [
        (
            r"((?:January|February|March|April|May|June|July|August|September|October|November|December)(?:[0-9, ]+))",
            "",
        )
    ]
    special_chars_patterns = [(r"\W", "")]
    strip_patterns = [(r"\n", " "), (r" +", " ")]
    extra_char_patterns = [(r"^[,. ]+|[,. ]+$", "")]
    comma_space_patterns = [(r"^[, ]+|[, ]+$", "")]
    space_patterns = [(r"^ +| +$", "")]
    roman_patterns = [(r"^[MDCLXVI](?:M|D|C{0,4}|L|X{0,4}|V|I{0,4})$", "")]
    abbreviation_patterns = [(r"^[JS][Rr]\.$", "")]
    page_patterns = [(r"\+page\[\d+\]\+", "")]

    def __init__(self, **kwargs):
        self.data_dir = kwargs.get("data_dir", BASE_DIR / "tools" / "gcl" / "data")
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        self.jurisdictions = kwargs.get("jurisdictions", None)
        self.suffix = kwargs.get("suffix", "")
        if not self.jurisdictions:
            try:
                with open(str(self.data_dir / "jurisdictions.json")) as f:
                    self.jurisdictions = kwargs.get("jurisdictions", json.load(f))
            except FileNotFoundError:
                raise Exception("jurisdictions.json not found")

    def gcl_parse(
        self,
        path_or_url: str,
        skip_patent=False,
        return_data=False,
        json_subdir=None,
        need_proxy=False,
    ):
        """
        Parses a Google case law page under an `html_path` or at a `url` and serializes/saves all relevant information
        to a json file.

        Args
        ----
        :param path_or_url: ---> str: a path to an html file or a valid url of a Google case law page.
        :param skip_patent: ---> bool: if true, skips downloading and scraping patent information.
        :param json_subdir: ---> str: name of the subdirectory in the 'json' folder to save the serialized
                                 data in. Defaults to `json_suffix`.
        """
        html_text = ""
        if not Path(path_or_url).is_file():
            html_text = tuple(self._get(path_or_url, need_proxy))[1]
        else:
            with open(path_or_url, "r") as f:
                html_text = f.read()

        html_text = BS(deaccent(html_text), "html.parser")

        opinion = html_text.find(id="gs_opinion")

        # Return empty set if case law page was not found (`404` error).
        if not opinion:
            print(f'Serialization failed for "{path_or_url}"')
            return {}

        opinion.find(id="gs_dont_print").replaceWith("")
        op_c = str(opinion)
        links = opinion.find_all("a")
        case_id = self.get_case_id(html_text)
        self._gcl_footnote_id(opinion)
        full_case_name = self._gcl_full_casename(opinion)
        opinion.find(id="gsl_case_name").replaceWith("")

        case = {
            "id": case_id,
            "full_case_name": full_case_name,
            "case_numbers": [],
            "citation": None,
        }

        case["citation"], court_info = self.gcl_citor(html_text)
        case["short_citation"] = []
        page_list = opinion.find_all("a", class_="gsl_pagenum")

        case["first_page"], case["last_page"] = None, None
        if page_list:
            pages = [p.get_text() for p in page_list]
            case["first_page"], case["last_page"] = int(pages[0]), int(pages[-1])

        for center in opinion.select("center > b"):
            case["short_citation"].append(center.get_text())
            center.replaceWith("")

        case["cites_to"] = {}
        for l in links:
            if fn := l.attrs:
                if fn.get("href", None) and "/scholar_case?" in fn["href"]:
                    case_citation = regex(l.get_text(), self.extra_char_patterns)
                    case_name = None
                    if gn := l.find("i"):
                        case_name = regex(gn.get_text(), self.comma_space_patterns)
                    id_ = regex(l.attrs["href"], self.case_patterns, sub=False)[0]
                    ct = {"name": case_name, "citations": [case_citation]}
                    if not case["cites_to"]:
                        case["cites_to"] = {id_: [ct]}
                    else:
                        if case["cites_to"].get(id_, None):
                            for el in case["cites_to"][id_]:
                                if el["name"] == case_name:
                                    if case_citation not in el["citations"]:
                                        el["citations"].append(case_citation)
                                else:
                                    case["cites_to"][id_] = [ct]
                        else:
                            case["cites_to"][id_] = [ct]
                    l.replaceWith(f" ####{id_} ")

        case["date"] = self._gcl_get_date(opinion)
        case["court"] = self.jurisdictions["court_details"][court_info]

        court_code = case["court"].get("court_code", None)
        jurisdiction = case["court"].get("jurisdiction", None)
        # Insert the case number if the case is still unpublished
        case["citation"] = case["citation"].replace(
            "XXXXXX", self._gcl_casenumber(html_text, jurisdiction, court_code, True)[0]
        )

        for id_, num_ in self._gcl_casenumber(opinion, jurisdiction, court_code):
            if fn := case["case_numbers"]:
                for el in fn:
                    if id_ == el["id"]:
                        el["docket_number"].append(num_)
                    else:
                        case["case_numbers"].append(
                            {"id": id_, "docket_number": [num_]}
                        )
                        break
            else:
                case["case_numbers"].append({"id": id_, "docket_number": [num_]})

        case["judges"] = self.gcl_get_judge(opinion, court_code, False)

        patents = []
        patent_numbers = self._gcl_get_patents(opinion)
        for key, value in self._gcl_get_claims(opinion).items():
            for patent_number in patent_numbers:
                if patent_number.endswith(key):
                    patent_found, claims = self.gcl_patent_data(
                        patent_number, case_id, skip_patent, True
                    )
                    patents.append(
                        {
                            "patent_number": patent_number,
                            "patent_found": patent_found,
                            "claims": claims,
                            "cited_claims": [
                                int(i)
                                for i in value
                                if regex(i, self.just_number_patterns, sub=False)
                            ],
                        }
                    )
                    break

        case["patents_in_suit"] = patents
        case["html"] = op_c
        case["training_text"] = ""
        case["footnotes"] = []

        small_tag = opinion.find_all("small")
        if small_tag:
            footnotes = small_tag[-1].find_all("a", class_="gsl_hash")
            for tag in footnotes:
                parent_tag = tag.parent
                # Remove footnote tag identifier from the end of case file.
                tag.replaceWith("")
                case["footnotes"].append(
                    {
                        "identifier": f"{tag.attrs['name']}",
                        "context": regex(parent_tag.get_text(), self.space_patterns),
                    }
                )

        self._gcl_replace_tags(opinion, court_code)

        case["training_text"] = regex(opinion.get_text(), self.strip_patterns)

        json_subdir = f"json_{self.suffix}" if not json_subdir else json_subdir
        with open(
            str(create_dir(self.data_dir / "json" / json_subdir) / f"{case_id}.json"),
            "w",
        ) as f:
            json.dump(case, f, indent=4)

        if return_data:
            return case

    def get_case_id(self, html_text):
        """
        Retrieve the case id given the html of the case file.
        """
        return regex(
            str(html_text.find(id="gs_tbar_lt")), self.case_patterns, sub=False
        )[0]

    def gcl_get_judge(self, opinion, court_code, just_locate=False):
        """
        Extract judge names from `opinion`.

        Args
        ----
        :param opinion: ---> BeautifulSoup object corresponding to html page of the opinion.
        :param court_code: ---> str: the court code to have further control on the parsed judge data.
        :param just_locate: ---> bool: just locate the tag containing judge names and return.
        """
        initial_cleaning_patterns = [
            (r"(?: +)?\+page\[\d+\]\+ +", ""),
            *self.judge_clean_patterns_1,
        ]
        judge_tag = ""
        for tag in opinion.find_all("p"):
            tag_text = regex(tag.get_text(), initial_cleaning_patterns)
            if regex(tag_text, self.judge_patterns, sub=False):
                judge_tag = tag
                break

        if just_locate:
            return judge_tag

        # Exclude the Supreme Court judges as it is not so useful.
        if judge_tag and court_code not in ["us"]:
            judges = regex(judge_tag.get_text(), initial_cleaning_patterns)
            judges = regex(
                judges,
                [
                    *self.judge_clean_patterns_2,
                    (" and ", ", "),
                    *self.extra_char_patterns,
                    *self.judge_clean_patterns_3,
                ],
                flags=re.I,
            )
            judges = regex(
                "".join(judges).split(","), [*self.comma_space_patterns, (r":", "")]
            )

            for i, person in enumerate(judges):
                if regex(person, self.roman_patterns, sub=False) or regex(
                    person, self.abbreviation_patterns, sub=False
                ):
                    judges[i - 1] = f"{judges[i-1]}, {person}"
                    judges.pop(i)
                elif not person:
                    judges.pop(i)

            judges = [
                " ".join(
                    [
                        l.lower().capitalize()
                        if not regex(l, self.roman_patterns, sub=False)
                        else l
                        for l in name.split()
                    ]
                )
                for name in [j for j in judges if j]
            ]
            judges = regex(
                judges,
                [
                    (
                        r"(?<=[\'’])\w|\b[a-z]+(?=\.)",
                        lambda match: f"{match.group(0).capitalize()}",
                    )
                ],
            )
            return judges
        return []

    def _abbreviate_court_name(self, name):
        """
        Abbreviate the court name according to the Bluebook format.
        """
        name = regex(name, [(r"([A-Z])(?![a-z])(?!\.)", r"\g<1>.")])
        for m_key in ["states_territories", "federal_courts"]:
            for key, value in self.jurisdictions[m_key].items():
                if key in name:
                    name = name.replace(key, value)
                    break
        return regex(name, [(r"([A-Z]\.) ([A-Z]\.)", r"\g<1>\g<2>")])

    def gcl_citor(self, data):
        """
        Create a bluebook citation for a given input html, url or path to an html file
        of a Google case law page.

        Args
        ----
        :param data: ---> str, BeautifulSoup obj: A url, path to the Google
                          case law page or its case_id or its BeautifulSoup object.
        """
        html_text = ""
        if isinstance(data, BS):
            html_text = data
        else:
            if not Path(data).is_file():
                html_text = tuple(self._get(data))[1]
            else:
                with open(data, "r") as f:
                    html_text = f.read()

        if not isinstance(html_text, BS):
            html_text = BS(html_text, "html.parser")

        citation = regex(
            html_text.find(id="gs_hdr_md").get_text(), self.extra_char_patterns
        )
        [court_name, court_type, state] = [""] * 3
        try:
            cdata = regex(citation, self.federal_court_patterns, sub=False)
            if not cdata:
                citation = regex(citation, [(r" ?[,-] ((\d{4}))$", r" (\g<1>)")])
                return citation, "Supreme Court"
            else:
                cdata = cdata[0]
            delimiter, court_name, year = cdata[1:]

            # Dist. Court by itself is vague. It defaults to District Court of D.C. or D.D.C.
            # E.g. x v. y, Dist. Court ---> x v. y, D.D.C.
            if court_name in ["Dist. Court"]:
                court_name = "D.D.C."
            else:
                fn = self.jurisdictions["federal_courts"].get(court_name, None)
                if fn is not None:
                    court_name = fn
                else:
                    # Fixes a district court that is only the state name.
                    # E.g. x v. y, Dist. Court, North Carolina ---> x v. y, D.N.C.
                    possible_court_type = regex(
                        citation.replace(cdata[0], "").split(",")[-1],
                        self.space_patterns,
                    )
                    state_abbr = self.jurisdictions["states_territories"][court_name]
                    if "Dist." in possible_court_type and state_abbr:
                        court_name = (
                            f"D. {state_abbr}"
                            if regex(state_abbr, [(r"[a-z]", "")], sub=False)
                            else f"D.{state_abbr}"
                        )
                    else:
                        raise KeyError

            court_name_spaced = f"{court_name} " if court_name else ""
            if not court_name:
                case_number = "" if delimiter == "-" else f", No. XXXXXX"
                date = year if delimiter == "-" else self._gcl_get_date(html_text, True)
                citation = regex(
                    citation.replace(
                        cdata[0], f"{case_number} ({court_name_spaced}{date})"
                    ),
                    self.strip_patterns,
                )
                return citation, "Supreme Court"

            # If Fed. Cl. and D.D.C. appear in a citation, add a placeholder ', Federal Courts'
            # to continue using the following lines without having to modify the regex.
            # E.g. x v. y, No 2021-2344 (Fed. Cl. 2021) ---> x v. y, No 2021-2344, Federal Courts (Fed. Cl. 2021)
            replace_with = (
                f" ({court_name_spaced}{year})"
                if court_name not in ["Fed. Cl.", "D.D.C."]
                else f", Federal Courts ({court_name_spaced}{year})"
            )
            citation = regex(
                citation.replace(cdata[0], replace_with), self.strip_patterns
            )
            cdata = regex(
                citation,
                [(r"( ?([-,]) ([\w:. \']+) \(([\w:. \']+)\))$", "")],
                sub=False,
            )[0]

            delimiter, court_type = cdata[1:3]
            court_type = self.jurisdictions["federal_courts"][court_type]
            court_type_spaced = f"{court_type} " if court_type else ""
            # Encountering a dash after publication in Google cases means that the case has been published.
            # So no case number is needed according to bluebook if a dash is encountered.
            case_number = "" if delimiter == "-" else f", No. XXXXXX"
            date = year if delimiter == "-" else self._gcl_get_date(html_text, True)
            citation = regex(
                citation.replace(
                    cdata[0],
                    f"{case_number} ({court_type_spaced}{court_name_spaced}{date})",
                ),
                self.strip_patterns,
            )

        except KeyError:
            [court_name, state, year] = [""] * 3
            cdata = regex(citation, self.state_court_patterns, sub=False)
            if not cdata:
                citation = regex(citation, [(r" ?[,-] ((\d{4}))$", r" (\g<1>)")])
                return citation, "Supreme Court"
            else:
                cdata = cdata[0]

            delimiter = ""
            for i, c in enumerate(cdata):
                if i == 1:
                    delimiter = c
                if i == 2:
                    state = regex(
                        c, [(r"\.", ""), (r"([A-Z-a-z])(?=[A-Z]|\b)", r"\g<1>.")]
                    )
                    # States which don't get abbreviated:
                    if c in ["Alaska", "Idaho", "Iowa", "Ohio", "Utah"]:
                        state = c
                elif i == 3:
                    d = c.split(",")[0]
                    court_name = self.jurisdictions["state_courts"][d]
                    # New York Supreme Court is cited as 'N.Y. Sup. Ct.'
                    if state == "N.Y." and d == "Supreme Court":
                        court_name = "Sup. Ct."
                elif i == 4:
                    year = c

            state_spaced = "" if "Commw" in court_name else f"{state} "
            court_name_spaced = f"{court_name} "
            case_number = "" if delimiter == "-" else f", No. XXXXXX"
            date = year if delimiter == "-" else self._gcl_get_date(html_text, True)
            citation = regex(
                citation.replace(
                    cdata[0], f"{case_number} ({state_spaced}{court_name_spaced}{date})"
                ),
                self.strip_patterns,
            )
        return citation, regex(
            " ".join([state, court_type, court_name]),
            [*self.space_patterns, *self.strip_patterns],
        )

    def _gcl_get_date(self, opinion, short_month=False):
        """
        Extract the decision date for a court `opinion` with the format `Day Month, Year`
        format and convert it to `Year-Month-Day`.

        Args
        ----
        :param short_month: ---> bool: if true, returns the date like `%b. %d, %Y`.
        """
        date = regex(
            opinion.find_all(
                lambda tag: tag.name == "center"
                and regex(tag.get_text(), self.date_patterns, sub=False)
            )[-1].get_text(),
            self.date_patterns,
            sub=False,
        )[0]

        date_format = datetime.strptime(regex(date, self.space_patterns), "%B %d, %Y")

        if short_month:
            date = date_format.strftime("%B %d, %Y")
            if not regex(date, [(r"May|June|July", "")], sub=False):
                date = date_format.strftime("%b. %d, %Y")
            return date

        return date_format.strftime("%Y-%m-%d")

    def gcl_handle_redundant(self, json_subdir=None, delete_redundant=False):
        """
        Print the redundant (unpublished) cases. Only keep the published case
        if `delete_redundant` is set to True.

        Args
        ----
        :param json_subdir: ---> str: name of the subdirectory in the 'json' folder that the serialized
                                 data is saved in. Defaults to `json_suffix`.
        :param delete_redundant: ---> bool: if True, will delete the serialized data and patent information.
        """
        suffix = self.suffix
        if not json_subdir:
            json_subdir = f"json_{self.suffix}"
            suffix = json_subdir.replace("json_", "")

        directory = self.data_dir / "json" / json_subdir
        json_files = list((directory).glob("*.json"))

        name_patterns, docket_patterns, ids = [], [], []
        for f in tqdm(json_files, total=len(json_files)):
            with open(f, "r") as jfile:
                info = json.load(jfile)
                dc = [info["date"]] + [info["court"]["court_code"]]
                name_patterns += ["".join([info["full_case_name"].lower()] + dc)]

                docket_patterns += [
                    "".join(
                        [
                            "".join(c["docket_number"]).lower()
                            for c in info["case_numbers"]
                        ]
                        + dc
                    )
                ]

                ids += [""] if info["short_citation"] else [info["id"]]

        patterns = name_patterns + docket_patterns
        indices = [
            fn
            for value in set(patterns)
            if len(fn := [i for i, v in enumerate(patterns) if v == value]) > 1
        ]
        ids.extend(ids)
        repeated_ids = set([ids[i] for i in set(reduce(concat, indices)) if ids[i]])
        print(f"There are {len(repeated_ids)} repeated cases in {str(directory)}")

        if delete_redundant:
            print("Starting to delete redundant (unpublished) cases...")
            for case_id in tqdm(repeated_ids, total=len(repeated_ids)):
                (directory / f"{case_id}.json").unlink()
                patent_folder = self.data_dir / "patent" / f"patent_{suffix}" / case_id
                if patent_folder.is_dir():
                    rm_tree(patent_folder)
                print(f"Data for case id {case_id} deleted successfully")
        else:
            redundent_cases = {
                x: self.gcl_get_citation(x, False)[x] for x in repeated_ids
            }
            print(redundent_cases)

    @staticmethod
    def _gcl_footnote_id(opinion):
        """
        Obtain all the footnote ids cited in the text and replace them with
        a unique identifier '@@@@[id]' for tracking purposes.
        """
        footnote_identifiers = opinion.find_all(
            lambda tag: tag.name == "sup" and tag.find("a")
        )
        if footnote_identifiers:
            for tag in footnote_identifiers:
                if tag.parent.attrs and tag.parent.attrs["id"] == "gsl_case_name":
                    tag.replaceWith("")
                    opinion.find_all("small")[-1].find(
                        lambda tag: tag.name == "p" and tag.find("a", class_="gsl_hash")
                    ).replaceWith("")
                else:
                    tag.replaceWith(
                        f" @@@@{tag.find('a').attrs['name'].replace('r', '')} "
                    )

    def _gcl_full_casename(self, opinion):
        """
        Extract full case name from the `opinion`.
        """
        return regex(
            opinion.find(id="gsl_case_name").get_text(),
            [*self.strip_patterns, *self.comma_space_patterns],
        )

    def _gcl_casenumber(
        self, opinion, jurisdiction=None, court_code=None, only_casenumber=False
    ):
        """
        Extract the case ids and docket numbers of any case related
        to `opinion`.

        Args
        ----
        :param only_casenumber: ---> bool: if True, return only the case numbers.
        """
        case_num = opinion.select_one("center > a")
        case_ids_ = [""]
        if fn := case_num.attrs:
            case_ids_ = regex(fn["href"], self.casenumber_patterns, sub=False)[0].split(
                "+"
            )

        docket_numbers = []

        if jurisdiction == "F":
            if court_code:
                if court_code not in ["us"]:
                    docket_numbers = regex(
                        case_num.get_text(), self.docket_appeals_patterns, sub=False
                    )
                elif court_code in ["us"]:
                    docket_numbers = regex(
                        case_num.get_text(), self.docket_us_patterns, sub=False
                    )

        if not docket_numbers:
            docket_numbers = regex(case_num.get_text(), self.docket_patterns).split(",")

        docket_numbers = regex(docket_numbers, self.extra_char_patterns)
        # Correct the docket numbers if they start with '-'
        for i, d in enumerate(docket_numbers):
            if d.startswith("-"):
                docket_numbers[i] = f'{docket_numbers[i-1].split("-")[0]}{d}'

        if only_casenumber:
            return docket_numbers

        dn = len(docket_numbers)
        ci = len(case_ids_)
        if dn > ci:
            return zip(case_ids_ * (dn - ci + 1), docket_numbers)

        return zip(case_ids_, docket_numbers)

    def _gcl_replace_tags(self, opinion, court_code):
        """
        Remove or replace all the tags with their appropriate labels.
        """
        for el in opinion.find_all("center"):
            el.replaceWith("")

        for h in opinion.find_all("h2"):
            if court_code in ["us"]:
                if "Syllabus" not in h.get_text():
                    h.replaceWith("")
            else:
                h.replaceWith("")

        for p in opinion.find_all("a", class_="gsl_pagenum"):
            p.replaceWith(f" +page[{p.get_text()}]+ ")

        for a in opinion.find_all("a", class_="gsl_pagenum2"):
            a.replaceWith("")

        for bq in opinion.find_all("blockquote"):
            text = bq.get_text()
            if text:
                bq.replaceWith(f" $qq$ {text} $/qq$ ")

        for pre in opinion.find_all("pre"):
            text = pre.get_text()
            if text:
                pre.replaceWith(f" $rr$ {text} $/rr$ ")

        # Locate tag with judge names and remove it along with every <p></p> coming before this tag.
        # Meant to clean up the text by removing the party names.
        judge_tag = self.gcl_get_judge(opinion, court_code, True)
        end_replace = False
        # Remove everything in the non-Supreme Court cases up to the paragraph with judge information.
        if judge_tag and court_code not in ["us"]:
            for p in opinion.find_all("p"):
                if not end_replace:
                    if p == judge_tag and judge_tag not in p.find_all("p"):
                        end_replace = True
                    if judge_tag not in p.find_all("p"):
                        p.replaceWith("")
                else:
                    break

        # Remove everything before Syllabus for Supreme Court cases.
        if court_code in ["us"]:
            for h in opinion.find_all(lambda tag: tag.name in ["p", "h2"]):
                if not end_replace:
                    if h.name == "h2":
                        if "Syllabus" in h.get_text():
                            end_replace = True
                        h.replaceWith("")
                    else:
                        if judge_tag:
                            if h == judge_tag and judge_tag not in h.find_all("p"):
                                end_replace = True
                            if judge_tag not in h.find_all("p"):
                                h.replaceWith("")
                else:
                    break

        for p in opinion.find_all("p"):
            text = p.get_text()
            if not text:
                p.replaceWith("")
            else:
                if regex(text, [(r"[.!?][\"\']?$", "")], sub=False):
                    p.replaceWith(f"{text} $$$$ ")
                else:
                    p.replaceWith(text)

        small = opinion.find_all("small")
        if small:
            small[-1].replaceWith("")

    def _gcl_get_patents(self, opinion):
        """
        Scrape the patent numbers cited in an `opinion`.
        """
        text = opinion.get_text()
        # Remove page numbers and line-breaks.
        modified_opinion = regex(text, [*self.page_patterns, *self.strip_patterns])
        patents = regex(modified_opinion, self.patent_number_patterns_1, sub=False)
        # Make sure that patterns like ("'#number patent") are there to sift through
        # extracted patents and keep the ones cited later in the case text.
        patent_refs = set(
            regex(modified_opinion, [(self.patent_reference_pattern, "")], sub=False)
        )
        if patent_refs:
            patents = [
                f"US{self.uspto_grab_patent_number(x)}"
                for x in patents
                if x[-3:] in patent_refs
            ]
        elif regex(text, [(r"[pP]atents-in-[sS]uit", "")]):
            patents = [f"US{self.uspto_grab_patent_number(x)}" for x in patents]

        return remove_repeated([x for x in patents if x != "US"])

    def uspto_grab_patent_number(self, number):
        """
        Grab the patent number (str) given an application `number` (str) from
        https://patentcenter.uspto.gov/. If `number` is a valid patent number,
        then return the cleaned patent number.

        Example
        -------
        >>> uspto_grab_patent_number('11/685,188')
        '7631336'

        >>> uspto_grab_patent_number('4,566,345')
        '4566345

        """
        clean_number = regex(
            number, [*self.extra_char_patterns, *self.special_chars_patterns]
        )

        if "/" in number:
            url = f"https://patentcenter.uspto.gov/#!/applications/{clean_number}"
            res_content = async_get(
                url, '//*[@id="maincontent"]/div/div/div/div[2]/div[2]/div[3]/div/a'
            )
            soup = BS(res_content, "html.parser")
            patent_number_a = soup.find(
                lambda tag: tag.name == "a"
                and tag.attrs.get("ng-bind", None) == "app.patentNumber()"
            )
            return (
                regex(patent_number_a.get_text(), self.special_chars_patterns)
                if patent_number_a
                else ""
            )

        return clean_number

    def _gcl_get_claims(self, opinion):
        """
        Extract the claim numbers cited in an `opinion`.
        """
        small_tag = opinion.find_all("small")
        footnotes_data, footnote_tags = {}, []
        if small_tag:
            footnotes = small_tag[-1].find_all("a", class_="gsl_hash")
            for tag in footnotes:
                parent_tag = tag.parent
                footnote_tags.append(tag.parent)
                tag.parent.replaceWith("")
                footnotes_data[tag.attrs["name"]] = regex(
                    parent_tag.get_text(), self.space_patterns
                )

        # Remove page numbers and well as line-breakers.
        modified_opinion = regex(
            opinion.get_text(),
            [(r" \d+\*\d+ ", " "), *self.page_patterns, *self.strip_patterns],
        )

        # Append the footnote tags back to the opinion.
        for tag in footnote_tags:
            opinion.small.append(tag)

        # Bring the footnote context in the text for keeping continuity.
        for key, val in footnotes_data.items():
            modified_opinion = modified_opinion.replace(f"@@@@{key}", val)

        modified_opinion = regex(modified_opinion, self.patent_number_patterns_2)
        # Regex to capture claim numbers followed by a patent number.
        claims_1 = re.findall(self.claim_patterns_1, modified_opinion)

        claims_from_patent = {}
        for c in claims_1:
            new_key = c[2]
            new_value = regex(
                c[1],
                [
                    (r"(\d+)[\- ]+(\d+)", r"\g<1>-\g<2>"),
                    (r"[^0-9\-]+", " "),
                    *self.strip_patterns,
                    *self.space_patterns,
                ],
            )
            if claims_from_patent.get(new_key, None):
                cls = claims_from_patent[new_key]
                if new_value not in cls:
                    cls += [new_value]
            else:
                claims_from_patent[new_key] = [new_value]

        patent_refs = re.finditer(self.patent_reference_pattern, modified_opinion)
        # Remove claim numbers of the type `claims # of the '# patent` to avoid double count.
        for c in claims_1:
            modified_opinion = modified_opinion.replace(c[1], "")
        # Regex to capture claim numbers at large or NOT followed by a patent number.
        claims_2 = re.finditer(self.claim_patterns_2, modified_opinion)

        ref_location = [
            (match.start(), match.group()) for match in patent_refs if match
        ]

        claims = {match.start(): match.group() for match in claims_2 if match}

        if ref_location:
            for key, value in claims.items():
                new_key = regex(
                    ref_location[closest_value([ref[0] for ref in ref_location], key)][
                        1
                    ],
                    [(r"[^0-9]+", "")],
                )
                new_value = regex(
                    value,
                    [
                        (r"(\d+)[\- ]+(\d+)", r"\g<1>-\g<2>"),
                        (r"[^0-9\-]+", " "),
                        *self.strip_patterns,
                        *self.space_patterns,
                    ],
                )
                if claims_from_patent.get(new_key, None):
                    cls = claims_from_patent[new_key]
                    if new_value not in cls:
                        cls += [new_value]
                else:
                    claims_from_patent[new_key] = [new_value]

        for key, value in claims_from_patent.items():
            value = [hyphen_to_numbers(x).split(" ") for x in value if x]
            if value:
                claims_from_patent[key] = sorted(
                    remove_repeated(reduce(concat, value)), key=sort_int
                )
        return claims_from_patent

    def gcl_patent_data(
        self, number_or_url: str, case_id=None, skip_patent=False, return_data=False
    ):
        """
        Download and scrape data for a patent with Patent (Application) No. or valid url `number_or_url`.

        Example
        -------
        >>> gcl_patent_data('https://patents.google.com/patent/US20150278825A1/en')

        Args
        ----
        :param skip_patent: ---> bool: if true, skips downloading patent and rather looks for
                                 a local patent file under `patents` folder.
        :param return_data: ---> bool: if true, returns the serialized downloaded data.
        """
        [patent_number, url] = [""] * 2
        info = {
            "patent_number": patent_number,
            "url": url,
            "title": None,
            "abstract": None,
            "claims": {},
        }
        subfolder = case_id

        if case_id:
            info["case_id"] = case_id

        else:
            subfolder = patent_number

        found = False

        try:
            if validate_url(number_or_url):
                url = number_or_url
                if fn := regex(url, [(r"(?<=patent/).*?(?=/|$)", "")], sub=False):
                    patent_number = fn[0]
        except:
            patent_number = number_or_url

        json_path = (
            self.data_dir
            / "patent"
            / f"patent_{self.suffix}"
            / subfolder
            / f"{patent_number}.json"
        )

        if json_path.is_file():
            if return_data:
                with open(json_path.__str__(), "r") as f:
                    info = json.load(f)
            found = True

        else:
            if not skip_patent:
                url = f"https://patents.google.com/patent/{patent_number}"
                status, html_text = self._get(url)
                if status == 200:
                    found = True
                    patent = BS(deaccent(html_text), "html.parser")
                    claim_tags = patent.select(".claims > *")
                    last_independent_num = 1
                    extra_count = 0
                    relevant_patterns = [*self.strip_patterns, *self.space_patterns]
                    for tag in claim_tags:
                        in_tag = tag.find(
                            lambda tag: tag.name == "div" and tag.attrs.get("num", None)
                        )
                        if in_tag:
                            num = in_tag.attrs["num"]
                            if "-" in num:
                                extra_count += 1  # Fixes wrong counting of claims.
                                num = int(regex(num, [(r"-.*$", "")])) + extra_count
                            else:
                                num = int(num) + extra_count
                            context = regex(
                                tag.get_text(),
                                [*relevant_patterns, (r"^\d+\. ", "")],
                            )
                            attach_data = {
                                "claim_number": num,
                                "context": context,
                                "dependent_on": None,
                            }
                            info["claims"][num] = attach_data
                            if "claim-dependent" not in tag.attrs["class"]:
                                last_independent_num = num
                            else:
                                info["claims"][num][
                                    "dependent_on"
                                ] = last_independent_num

                    abstract_tags = patent.find_all("div", class_="abstract")
                    abstract = " ".join(
                        [
                            regex(ab.get_text(), relevant_patterns)
                            for ab in abstract_tags
                        ]
                    )
                    info["abstract"] = abstract
                    info["title"] = regex(
                        patent.find("h1", attrs={"itemprop": "pageTitle"}).get_text(),
                        [*relevant_patterns, (r" - Google Patents|^.*? - ", "")],
                    )
                    info["url"] = url
                    info["patent_number"] = patent_number
                    print(f"Saving patent data with Patent No. {patent_number} ...")
                    create_dir(json_path.parent)
                    with open(json_path.__str__(), "w") as f:
                        json.dump(info, f, indent=4)

        if return_data:
            if info["title"] is None:
                print(
                    f"Patent No. {patent_number} has not been downloaded yet. Please set `skip_patent=False`"
                )
                return found, []

            return found, info["claims"]

    def gcl_get_citation(self, case_id, return_list=True):
        """
        Given a `case_id` for a Google case law page and create a summary
        of the case in terms of its bluebook citation, court and date.
        If case is not found in the local database, download it first.

        Args
        ----
        :param return_list: ---> bool: if False, return a dictionary instead with keys being
                                 `citation`, `court` and `date`.
        """
        data = {"citation": None, "court": None, "date": None}
        path_to_file = (
            self.data_dir / "json" / f"json_{self.suffix}" / f"{case_id}.json"
        )
        url = self.base_url + f"scholar_case?case={case_id}"
        if not path_to_file.is_file():
            data = self.gcl_parse(url, return_data=True)
        else:
            with open(path_to_file, "r") as f:
                data = json.load(f)

        case_summary = []
        if not return_list:
            case_summary = {case_id: {}}

        if data:
            data["url"] = url
            for key, val in data.items():
                if key in ["citation", "date", "court", "url"]:
                    if return_list:
                        if key == "court":
                            case_summary += val.values()
                        else:
                            case_summary.append(val)
                    else:
                        case_summary[case_id][key] = val
        return case_summary

    def _collect_cites(self, data: str) -> list:
        """
        Collect the value of `cites_to` key in a gcl `data`. If file was not found,
        it will be downloaded.

        Args
        ----
        :param data: ---> str or pathlib: path to a gcl json file or a valid case_id.
        """
        case_repo, case_id = {}, ""
        if isinstance(data, str):
            if regex(data, self.just_number_patterns, sub=False):
                top_folder = self.data_dir / f"json_{self.suffix}"
                for folder in top_folder.glob("**"):
                    json_path = top_folder / folder / f"{data}.json"
                    if json_path.is_file():
                        with open(json_path.__str__(), "r") as f:
                            case_repo = json.load(f)
                        break

                if not case_repo:
                    case_id = data
                    url = self.base_url + f"scholar_case?case={case_id}"
                    case_repo = self.gcl_parse(url, return_data=True)

        if isinstance(data, Path):
            with open(data.__str__(), "r") as f:
                case_repo = json.load(f)
                case_id = case_repo["id"]

        return remove_repeated([case_id] + list(case_repo["cites_to"].keys()))

    def bulk_cites_collect(self, json_subdir=None):
        """
        Collect case_ids collected using the method `_collect_cites`
        from each case found in the subdirectory `~/json/json_subdir`.
        If no subdirectory is set, it defaults to `json_suffix`.
        """
        filename = self.suffix
        if not json_subdir:
            json_subdir = f"json_{self.suffix}"
            filename = json_subdir.replace("json_", "")
        case_ids = create_dir(self.data_dir) / "json" / f"case_ids_{filename}.json"

        r = {"case_ids": []}
        if case_ids.is_file():
            with open(case_ids.__str__(), "r") as f:
                r = json.load(f)
        paths = (self.data_dir / "json" / json_subdir).glob("*.json")
        r["case_ids"] += reduce(
            concat,
            list(multiprocess(self._collect_cites, list(paths), yield_results=True)),
        )
        r["case_ids"] = remove_repeated(r["case_ids"])
        with open(case_ids.__str__(), "w") as f:
            json.dump(r, f, indent=4)

    def _get(self, url_or_id, need_proxy=False):
        """
        Request to access the content of a Google Scholar case law page with a valid `url_or_id`.

        Args
        ----
        :param need_proxy: ---> bool: if True, start switching proxy IP after each request
                                      to reduce risk of getting blocked.
        """
        url = url_or_id
        if regex(url_or_id, self.just_number_patterns, sub=False):
            url = self.base_url + f"scholar_case?case={url_or_id}"

        res_content = ""

        if need_proxy:
            proxy = proxy_browser()
            proxy.get(url)
            res_content = proxy.page_source
            status = 200

            if not res_content:
                status = 0

            else:
                # Obtain a `404` error indicator if server returned html.
                if regex(res_content, [(r"class=\"gs_med\"", "")], sub=False):
                    status = 404
                # Solve recaptcha if encountered.
                elif regex(res_content, [(r"id=\"gs_captcha_c\"", "")], sub=False):
                    EXPECTED_RESULT = "You are verified"
                    recaptcha = recaptcha_process(url, proxy)
                    assert EXPECTED_RESULT in recaptcha
                switch_ip()

        else:
            response = requests.get(url)
            response.encoding = response.apparent_encoding
            status = response.status_code
            if status == 200:
                res_content = response.text

        if status == 404:
            print(f'URL "{url}" not found')

        if status not in [200, 404]:
            raise Exception(f"Server response: {status}")
        return status, res_content
