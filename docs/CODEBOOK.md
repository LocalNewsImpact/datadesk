# CIN coding codebook

Transcribed from *Mizzou ML News Labeling CODEBOOK* (PDF), which the
manual coding cohorts A–E worked from and which produced the 1,003
articles the production classifier was trained on.

Kept here because the definitions are the specification the model is
built on: what a category means is not recorded in the model, the
database or the pipeline, and until now it lived in a PDF attachment.

Everything above the horizontal rule is the codebook as written.
Annotations after it are this repository's, and are marked as such.

---

## Definitions

These definitions and discussion are taken from Friedland et al. 2016
with updates reflecting the objectives of the current research project.

A goal of the Critical Information Needs (CIN) framework is to enable
researchers to better understand the volume and breadth of the news
provision within a geographic community. Each category represents a
segment of the information portfolio residents need to understand, live
and take action within their community.

Communities need to coordinate a range of activities, from elections to
emergency response. They need to solve problems in health, education and
economic development. They need to establish systems of public
accountability and develop a sense of connectedness.

We are proposing to adapt and extend CIN to include Community
Information Needs, to allow inclusion of content less identified with
accountability journalism, but still aligned with the day-to-day
experiences and interests of residents. The full set of categories:

1. Emergencies and risks, both immediate and long term
2. Health and welfare, including specifically local health information
   as well as group specific health information where it exists
3. Education, including the quality of local schools and choices
   available to parents
4. Transportation, including available alternatives, costs, and schedules
5. Economic opportunities, including job information, job training, and
   small business assistance
6. The environment, including air and water quality and access to
   recreation
7. Civic information, including the availability of civic institutions
   and opportunities to associate with others
8. Civic life, including information and advice related to daily
   activities and planning
9. Political information, including information about candidates at all
   relevant levels of local governance, and about relevant public policy
   initiatives affecting communities and neighborhoods
10. Sports, including games

### The action test

> When classifying articles into categories, consider what specific
> understanding or action — on balance — might be undertaken as a result
> of the content.

Depending on the category, an article might help a reader:

| the reader could | category |
| --- | --- |
| Know where to seek shelter from a tornado | Emergencies |
| Learn where to get a flu vaccine | Health |
| Decide which school to send their children | Education |
| Plan their bus route across town | Transportation |
| Pick a restaurant for lunch | Civic Life |
| Find a new job opportunity | Economics |
| Volunteer at the library | Civic Info |
| Select their candidate in the next city election | Political Life |
| Plant sustainable native flora in their front yard | Environment |
| Check the box score from last night's game | Sports |

## The ten categories, in full

**1. Emergencies and Public Safety.** Individuals, neighborhoods, and
communities need access to emergency information on platforms that are
universally accessible and in languages understood by the large majority
of the local population, including information on dangerous weather;
environmental and other biohazardous outbreaks; and public safety
threats, including terrorism, amber alerts, and other threats to public
order and safety. Further, all citizens need access to local (including
neighborhood) information on policing and public safety.

**2. Health.** All members of local communities need access to
information on local health and healthcare, including information on
family and public health in accessible languages and platforms;
information on the availability, quality, and cost of local health care
for accessibility, lowering costs, and ensuring that markets function
properly, including variations by neighborhood and city region; the
availability of local public health information, programs, and services,
including wellness care and local clinics and hospitals; timely
information in accessible language on the spread of disease and
vaccination; timely access to information about local health campaigns
and interventions.

**3. Education.** Local communities need access to information on all
aspects of the local educational system, particularly during a period
when local education is a central matter for public debate,
decision-making, and resource allocation, including: the quality and
administration of local school systems at a community-wide level; the
quality of schools within specific neighborhoods and geographic regions;
information about educational opportunities, including school
performance assessments, enrichment, tutoring, afterschool care and
programs; information about school alternatives, including charters;
information about adult education, including language courses, job
training, and GED programs, as well as local opportunities for higher
education.

**4. Transportation Systems.** All members need timely information about
local transportation across multiple accessible platforms, including:
information about essential transportation services including mass
transit at the neighborhood, city, and regional levels; traffic and road
conditions, including those related to weather and closings; timely
access to public debate on transportation at all layers of the local
community, including roads and mass transit.

**5. Environment and Planning.** Local communities need access to both
short and long-term information on the local environment, as well as
planning issues that may affect the quality of lives in neighborhoods,
cities, and metropolitan regions, including: the quality of local and
regional water and air, timely alerts of hazards, and longer term issues
of sustainability; the distribution of actual and potential
environmental hazards by neighborhood, city region, and metropolitan
area, including toxic hazards and brownfields; natural resource
development issues that affect the health and quality of life and
economic development of local communities; information on access to
environmental regions, including activity for restoration of watersheds
and habitat, and opportunities for recreation.

**6. Economic Development.** Individuals, neighborhoods, and communities
need access to a broad range of economic information, including:
employment information and opportunities within the local region; job
training and retraining, apprenticeship, and other sources of reskilling
and advancement; information on small business opportunities, including
startup assistance and capital resources; information on major economic
development initiatives affecting all local levels.

**7. Civic Information.** Communities need information about major civic
institutions, nonprofit organizations, and associations, including their
services, accessibility, and opportunities for participation in:
libraries and community-based information services.

**8. Civic Life.** Residents need information about things to do and
places to go in town that are related to not just active participation,
but also consumption of cultural arts and services; recreational
opportunities; nonprofit groups and associations; community-based social
services and programs; and religious institutions and programs and
profiles of the people and places involved in these programs and
offerings.

**9. Political Life.** In a federal democracy, citizens need information
on local, regional, and county candidates at all units of governance,
including: information on elected and voluntary neighborhood councils;
school boards; city council and alder elections; city regions; and
county elections; timely information on public meetings and issues,
including outcomes; information on where and how to register to vote,
including requirements for identification and absentee ballots;
information on state-level issues where they impact local policy
formation and decisions.

**10. Sports.** In many communities, sports are a business, a cultural
force, a source of shared conversation and debate, and an event. Sports
information can include game stories, profiles of athletes, and coverage
of organizations and facilities closely tied to athletic competition.

## Tags

The ten categories above, plus four that appear in the **primary** menu
only:

- **NOT LOCAL** — a wire story or other non-locally written content
- **NO APPROPRIATE CATEGORY** — you cannot clearly assign a story to any
  of the available labels
- **TOO SHORT** — not enough content (headline and article text) to
  evaluate
- **TECHNICAL ERROR** — the headline and article text are mismatched,
  article text is missing or otherwise unreadable

## Signals

Use these two fields to inform your estimation of the appropriate
categorizations: **Headline**, **Article Text**.

## Procedure

Assign a **primary** category — required. If the story includes multiple
categories, use **secondary** for the next most relevant. **Only add a
secondary category if you have significant confidence in its presence in
the story.**

A third tier, "also present", was offered and was used on 15 of 500
rows.

## Worked examples

| category | example headline |
| --- | --- |
| Emergencies and Public Safety | Several fire departments respond to fire near Jonesburg |
| Health | Far from equal: Rural Missourians have less medical care than they did 100 years ago |
| Education | More school districts in Missouri are switching to a four-day week |
| Transportation Systems | Pavement maintenance scheduled for East Broadway and Green Meadows Road |
| Environment and Planning | Columbia's chilling Halloween weekend |
| Economic Development | Boone County plans a second round of ARPA applications |
| Civic Information | Despite challenges, city opens mobile shower trailer to homeless community |
| Civic Life | An inside look at Fall Into Art, a showcase of local artists |
| Political Life | Election petitions available to run for Second and Sixth Ward |
| Sports | Moberly advances to district semifinals to face Centralia; Marshall upsets Van Horn |

---

## Annotations

Not part of the codebook. Recorded here because they are findings about
it, measured from the coding data it produced. See
`docs/CLASSIFICATION_REVIEW_QUEUE.md` for the full analysis.

**Civic Information and Civic Life collide by definition.** Definition 7
names "nonprofit organizations, and associations"; definition 8 names
"nonprofit groups and associations". Both cover community-based services
and programs. A story about a nonprofit satisfies each by its literal
text, so two careful coders will file it differently.

Measured over 813 comparable pairs from cohorts A–D: when one coder used
**Civic information**, the other did not use it at all — primary or
secondary — in **85.6%** of cases. Sports agreed 93.9% on the same
protocol. This is a property of the definitions, not of the coders.

**The worked examples collide too.** The Civic Information example —
"city opens mobile shower trailer to homeless community" — is a
community-based social service, which definition 8 explicitly claims for
Civic Life.

**The short-form list draws the line the expanded definitions lose.**
Item 7 is "opportunities to associate with others"; item 8 is "daily
activities and planning". With the action test — volunteer at the
library versus pick a restaurant — that is a usable distinction:
**participation versus consumption**.

**Civic information is a catch-all, not half of an overlapping pair.**
Measured over the same 813 comparisons, its disagreements are spread
across five categories rather than concentrated against Civic Life:

| Civic information disagrees with | share of its disagreements |
| --- | ---: |
| Emergencies and Public Safety | 27% |
| Civic Life | 17% |
| Economic Development | 17% |
| Political life | 11% |
| Transportation Systems | 9% |

It agrees 12% of the time. Civic Life agrees 39%, and a third of *its*
disagreements are with that one category. So Civic Life is a
comparatively coherent category being pulled into a diffuse one, and
not an equal partner in a mutual overlap.

The definition explains the behaviour. "Major civic institutions,
nonprofit organizations, and associations, including their services,
accessibility, and opportunities for participation" makes any story
about an institution eligible — a fire department, a transit agency, a
business grant programme, a city council are all institutions, so each
reads as Civic Information to a careful coder.

**Merging the two is not the repair.** Civic Life is 25% of the corpus
and Civic information 17%; one category covering 42% of local coverage
answers no question anybody would ask, and Civic Life is the more
descriptive of what local newsrooms actually publish — events, arts,
recreation, religious and community life. That is worth keeping
separable.

**Suggested repair: narrow Civic information to access, not activity.**
The codebook's own action test does the work — what could a reader *do*:

| story | category |
| --- | --- |
| The fire department responds to a fire | Emergencies |
| How to join the volunteer fire department | Civic Information |
| The library board votes on opening hours | Political Life |
| The library's new hours, and how to get a card | Civic Information |
| A gallery opening this weekend | Civic Life |

Civic Information is about **reaching or joining an institution**; the
other nine cover what institutions and people **do**. Nonprofits and
associations are then assigned by that test rather than named in both
definitions: a volunteer drive is Civic Information, a fundraising gala
is Civic Life.

Re-measure agreement on 200–300 articles under the revised wording
before committing to a larger programme.

---

## Annotations, second pass

Written while planning a targeted re-labelling of Civic Life. The
findings above stand; these add to them.

### Two agreement numbers that do not conflict

The 12% above is **pairwise agreement** — how often a second coder used
the same category, over 813 comparable pairs from cohorts A–D. A
separate figure, **mean coder confidence** in the aggregated set, puts
Civic information and Civic Life together at 0.84, which is mid-pack:

| label | mean confidence | n |
| --- | ---: | ---: |
| Economic Development | 0.80 | 69 |
| Political life | 0.81 | 59 |
| Education | 0.82 | 31 |
| Civic information | 0.84 | 72 |
| Civic Life | 0.84 | 101 |
| Sports | 0.87 | 139 |

These measure different things and both are true. Confidence is the
aggregation's own certainty about a resolved label; pairwise agreement
is whether two people reached the same one. A category can be resolved
confidently *after* the fact and still be the one coders diverge on.
Quote the pairwise number when the question is whether the definition
works, and the confidence number when the question is how much to trust
a particular row.

### The bigger leak is not the boundary

The ten least-confident articles in each category are mostly not
Civic/Civic confusions. They are material that should have been
rejected:

**Civic Life** — three obituaries, three non-local (imprisoned Eritrean
Christians, the 22nd anniversary of 9/11, Catholics with disabilities),
one advertorial ("Everything You Must Know Before Buying a Used Jeep").

**Civic information** — an obituary, a recipe ("the perfect summer
spaghetti from pantry staples"), Microsoft laying off 10,000, "tips for
parents whose kids struggle to make friends".

A coder with no fitting option reaches for the broadest-sounding
category, and Civic Life is it. That is a larger source of noise in
these two labels than the overlap between them, and it is invisible to
any repair aimed at the boundary.

Three gaps in the tag list explain it. There is no tag for an
**obituary**, none for **lifestyle or service copy** — recipes, listicles,
consumer advice — and none for **advertorial**. `NO APPROPRIATE
CATEGORY` covers all three in principle and is reached for by nobody,
because a story about a church supper plainly *has* an appropriate
category and the coder is not being asked whether it is news.

`NOT LOCAL` also needs to say that a national story carrying a local
byline or dateline is not local. Three of Civic Life's ten weakest are
that shape.

### On renaming Civic Life to Community Life

Worth doing, and the argument is in the codebook's own introduction:

> We are proposing to adapt and extend CIN to include **Community
> Information Needs**, to allow inclusion of content less identified with
> accountability journalism, but still aligned with the day-to-day
> experiences and interests of residents.

That sentence is the Civic Life definition. The category is already the
*community* half of the framework, and naming it so states what the
project decided rather than changing it.

It also removes a shared prefix that carries no information. A coder
scanning a menu reads "Civic…" twice and cannot tell the two apart until
the second word, which is exactly the moment the definitions stop
helping. `Community Life` against `Civic Information` separates on the
first word.

**Rename the display, not the class.** The label string is the model's
class name: `cin_labels.LABELS` is the order `label2id` is built from in
the shipped checkpoint, and `docs/CIN_MODEL_BASELINE.md` records what
happens when that order is taken from anything else. A rename that
reaches the stored value silently misaligns every existing label and the
model trained on them. Map `Civic Life` to a display name; leave the
vocabulary alone until a retrain deliberately changes it.

### What a targeted pass should draw

Not a random sample. The two categories are 101 and 72 of the 950
labelled articles — 11% and 8% — so a random draw spends four fifths of
the effort on articles nobody is unsure about.

(The first annotation puts them at 25% and 17%. That is a different
denominator — a share of the corpus rather than of this labelled set —
and the two are not interchangeable. Neither has been reconciled against
the other; use the one whose base is stated.)

Draw instead:

1. Articles either coder put in **Civic information or Civic Life**,
   primary or secondary, where the two disagreed. That is the boundary
   itself.
2. Articles labelled either category with **confidence below 0.8** —
   50 of the 173 across the two, verified against the aggregated set,
   and where the rejects are hiding.
3. A **random slice of the rest**, kept small, as the only unbiased
   estimate of whether the revision helped rather than moved the problem.

Re-measure pairwise agreement, not confidence: the question is whether
the revised definitions let two people reach the same answer.
