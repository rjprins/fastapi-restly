---
blogpost: true
date: 2026-07-22
category: meta
---

# Why I made FastAPI-Restly

Four years ago I joined the team at ClearBlue Markets. They had started a new project where customers could see their position on CO2 credits markets.
CO2 credit markets and EU emission rules is a fascinating and promising niche, btw.
The project involved both customer-scoping of data and typical admin interface needs.

The natural first instinct is to look for a fastapi-admin or a fastapi-rest package to save yourself a bunch of work. However, surprisingly there weren't any libraries that really did what I wanted: Provide CRUD interfaces quickly, while still being extensible. `fastapi-utils` and its `@cbv` decorator was the main go-to at the time. But the class decorator approach did not enable code reuse in the way I wanted.
`fastapi-crudrouter` had the same problem and so did basically every other package that I reviewed.

The core of the issue lies with how FastAPI works. By using function signatures and annotations for HTTP contracts and dependencies, routes are very much tied to functions.

I don't really remember what I knew and didn't know when deciding to write my own. But for sure I greatly underestimated the effort required to get proper class-based behavior for FastAPI. In retrospect it was a bit of a crazy approach for a small company with only a single engineering team.

In my mind the only alternatives seemed to be copy-pasting endpoints or trying to wrestle customizations into opaque endpoint generators. Those envisioned unbearable pains drove me to go deep into annotations, function signature rewriting, mro walking, and other subjects you really shouldn't be working with in any normal business application.

I only worked there for one year, but that was enough time to get the main machinery working and apply it extensively. I got their permission to open source it, and always intended to, it only took four years to get here.

In the meantime I got employed at Global Data & Analytics for Brenntag; a large chemicals distributor. Here was a FastAPI application that suffered the exact same pains, and chose the copy-paste route. I wouldn't say it was a mess, care had been taken to keep things clean on a code level, but there was no REST. Every endpoint was bespoke. Filters on listings were bespoke. POST endpoints bespoke. The endpoint paths were unpredictable. Etc etc.

You can't step in as a new engineer in a team and say "Hey let's use this vague non-open source bunch of code that only I know about". So the project got side-lined at this point.

Luckily, half a year later I could join a brand new "Innovation Lab" team; short-term projects, one frontender, and one backender. I asked my manager if I could use my project. He gave the OK, although I'm not entirely sure he fully understood to what he agreed. This was a great chance to dog-food the project, and see if it was still useful in a different context.

Maybe at this point I should explain that this is not actually my first framework. A long time ago, I wrote a similar framework for Flask. But the hard technical stuff was solved by `flask-classy` <3. This was at EclecticIQ, 2016, a cyber security startup that I was very lucky to be one of the first engineers for. Back then I didn't consciously write a framework, it was just a base class to base all other view classes on. With a few smart overrides (or "seams" in modern LLM parlance) we could effectively customize most of what we wanted. That base class is the main inspiration for the current override design of Restly. We created over 50 resources each with their own twists and tweaks, because for some reason, in real applications, **nothing is ever really just plain "CRUD"**.

Today, I am again working with FastAPI at yet another company, Greenchoice. As an IC freelancer, I will have to convince the tech lead of the quality, reliability, and long-term maintenance commitment of Restly. This is for a business-critical application so the bar is high. I will keep you posted.

This is a new project and the pains I see are now extended to what Restly also solves if you want it: Database setup and alembic test fixtures. Small things, but everybody is again generating their own little versions and we just shouldn't have to. Also, some things are not that small or simple actually, like effective savepoint sessions for testing.

Meanwhile, I will keep improving, polishing, and extending Restly. I checked and the solution I want still isn't there in 2026. Only Django/DRF really matches what I am looking for, but that topic is for another post.
