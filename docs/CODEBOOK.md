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

**Suggested repair**, before any further coding at scale: restate 7 and
8 around the action test, and assign nonprofits and associations to one
of them by that test rather than naming them in both. A nonprofit's
volunteer drive is Civic Information; its fundraising gala is Civic
Life. Then re-measure agreement on 200–300 articles before committing to
a larger programme.
