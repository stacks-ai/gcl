
![gcl|test](https://github.com/StacksLaw/gcl/actions/workflows/tests.yml/badge.svg)


# gcl

This package provides a scraper/parser for Google case law pages available at https://scholar.google.com/.

The offered features include scraping, parsing, serializing
and tagging important data such as bluebook citations, judge names, courts,
decision dates, case numbers, patents in suit, cited claims, footnotes, etc.

# Getting started

### Install

After cloning the Github repo `gcl`, you should go to the directory where the repo is downloaded, create a virtual python environment and run the following command on the terminal:

`pip install .`

to install the package. Make sure that once `selenium` is installed, you have the chrome driver installed as well. For macOS, we can do this by executing the following command on the terminal:

`brew install chromedriver --cask`

### Test

To run gcl's test unit, just run the following command:

`python -m unittest discover`

### Try

To scrape and download a case law page, let us choose a case law page,

https://scholar.google.com/scholar_case?case=9862061449582190482

and use the following snippet to get things started:

```
from gcl.main import GCLParse

GCL = GCLParse(data_dir="/users/.../gcl_test", suffix="test_v1")

case_law_url = "https://scholar.google.com/scholar_case?case=9862061449582190482"

GCL.gcl_parse(case_law_url, skip_patent=False, return_data=False, need_proxy=False, random_sleep=False,)
```

Running this code will save a JSON file `9862061449582190482.json` under the directory `/users/.../gcl_test/data/json/json_test_v1` with the following data structure:

```
{   
                "id": None,
                "full_case_name": None,
                "case_numbers": [],
                "citation": None,
                "short_citation": [],
                "first_page": None,
                "last_page": None,
                "cites_to": {},
                "date": None,
                "court": {},
                "judges": [],
                "personal_opinions": {},
                "patents_in_suit": [],
                "html": None,
                "training_text": None,
                "footnotes": [],
}
```

**Note:** `need_proxy` is advised to be set to `False` for now since it is still experimental and therefore not recommended to be turned on in this preliminary version. This has the caveat that we cannot run the `GCL` instance in parallel to make multiple queries to Google Scholar as your IP will be temporarily blocked. In the next version, we will make multiple parallel queries operational.
