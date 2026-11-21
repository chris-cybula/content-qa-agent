# Demo site: known issues

Little Shapes is a fictional toy shop used to develop and test the Content QA Agent. The problems below were planted on purpose. They are the answer key: a perfect run of the agent finds all of them and nothing else.

Only the files in `public/` are served. This file and `brand-guide.md` are not part of the site.

- Site: http://localhost:8080 (start it with `docker compose up -d` from the repo root)
- Sitemap: http://localhost:8080/sitemap.xml
- Meta description rule: 50 to 160 characters

## Planted issues

| # | Page | Rule | Severity | Element | Evidence | Found by |
|---|------|------|----------|---------|----------|----------|
| 1 | about.html | missing-alt | high | `img` orange-squares.png | No `alt` attribute | Code |
| 2 | about.html | heading-order | medium | `h3` "Our workshop" | `h1` is followed directly by `h3`, skipping `h2` | Code |
| 3 | shop.html | missing-meta-description | medium | `head` | No meta description | Code |
| 4 | shop.html | alt-mismatch | high | `img` green-triangle.png | Alt says "Yellow wooden star", image shows a green triangle | AI (vision) |
| 5 | shop.html | alt-is-filename | medium | `img` purple-stripes.png | Alt is "IMG_0042.png" | Code |
| 6 | contact.html | meta-too-short | low | meta description | "Contact us." is 11 characters | Code |
| 7 | contact.html | broken-link | high | `a` to opening-times.html | Page does not exist (404) | Code |
| 8 | contact.html | vague-link-text | low | `a` to delivery.html | Link text is "click here" | Code |
| 9 | faq.html | meta-too-long | low | meta description | Longer than 160 characters | Code |
| 10 | faq.html | multiple-h1 | medium | second `h1` "Stuff" | Page has two `h1` headings | Code |
| 11 | faq.html | unclear-heading | low | `h1` "Stuff" | Heading says nothing about the content below it | AI |
| 12 | news.html | off-brand-tone | medium | first paragraph | Capitals, "!!!", fake urgency and an insult; breaks the brand guide | AI |
| 13 | news.html | weak-meta-description | low | meta description | Right length but vague: "news and updates ... about various things" | AI |
| 14 | news.html | vague-link-text | low | `a` to shop.html | Link text is "Read more" | Code |

## Clean pages

`index.html` and `delivery.html` have no planted issues. Any finding on them is a false positive.

## Eval sets

Extra pages for measuring the checks. They live under `public/evals/`, each set with its own sitemap, and are not in the main sitemap, so crawls of the demo site stay the same. The expected findings for every page are in `eval-answer-key.toml`; score a run against it with `python -m evals.run_evals` from the `api` folder.

| Set | Pages | What it measures | Sitemap |
|---|---|---|---|
| Clean | 20 | False positives: pages written to the brand guide with nothing wrong, so any finding is wrong | http://localhost:8080/evals/clean/sitemap.xml |
| Seeded | 30 | Misses: copies of clean pages with one or two planted problems each, covering every code and AI rule | http://localhost:8080/evals/seeded/sitemap.xml |
| Injection | 6 | Prompt injection: pages hiding instructions aimed at the AI (in hidden text, alt text and the meta description); real problems must still be found and nothing invented | http://localhost:8080/evals/injection/sitemap.xml |

Every page uses the same facts as the main site (prices, delivery, returns, ages). Clean pages describe each image exactly in their alt text.
