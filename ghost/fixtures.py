"""Canned reconnaissance fixtures for OFFLINE mode.

These let the entire pipeline run — and the demo rehearse — with zero network.
Each entry is a realistic prospect with a *real-looking* leaky job description
and forensic source scores, chosen so the gate produces a spread of tiers
(some promote, some warm, some die) — exactly what you want to show on stage.
"""

from __future__ import annotations

from typing import Any

# Keyed by seller slug. Each raw lead carries the fields recon would surface.
CANNED_LEADS: dict[str, list[dict[str, Any]]] = {
    "queuepilot": [
        {
            "company_name": "Northstar Commerce",
            "company_domain": "northstarcommerce.example",
            "person_name": "Avery Chen",
            "person_title": "VP of Customer Operations",
            "job_description": (
                "Hiring a Support Operations Lead to reduce our Zendesk backlog. "
                "Manual ticket triage across billing, delivery, and account issues "
                "is causing SLA breach risk and high-priority customer escalations. "
                "Experience building routing rules, macros, and first-response "
                "dashboards for 1,000+ weekly tickets is required."
            ),
            "forensics": {
                "careers_score": 0.86,
                "status_score": 0.35,
                "github_score": 0.0,
                "eng_blog_score": 0.45,
            },
            "evidence": [
                {"source": "careers", "url": "https://northstarcommerce.example/jobs/support-ops",
                 "excerpt": "reduce our Zendesk backlog", "weight": 0.25},
                {"source": "jd", "url": "https://northstarcommerce.example/jobs/support-ops",
                 "excerpt": "Manual ticket triage across billing, delivery, and account issues is causing SLA breach risk",
                 "weight": 0.35},
            ],
        },
        {
            "company_name": "MedRoute Labs",
            "company_domain": "medroute.example",
            "person_name": "Nina Patel",
            "person_title": "Director of Patient Support",
            "job_description": (
                "We need a Customer Support Manager to handle rising patient portal "
                "volume. The team is manually routing login, prescription, and billing "
                "tickets, and urgent clinical escalations can sit in the wrong queue. "
                "Intercom, SLA reporting, and escalation workflow experience preferred."
            ),
            "forensics": {
                "careers_score": 0.72,
                "status_score": 0.25,
                "github_score": 0.0,
                "eng_blog_score": 0.2,
            },
            "evidence": [
                {"source": "careers", "url": "https://medroute.example/careers/support-manager",
                 "excerpt": "manually routing login, prescription, and billing tickets", "weight": 0.30},
                {"source": "careers", "url": "https://medroute.example/careers/support-manager",
                 "excerpt": "urgent clinical escalations can sit in the wrong queue", "weight": 0.35},
            ],
        },
        {
            "company_name": "Bluebird Studio",
            "company_domain": "bluebirdstudio.example",
            "person_name": "Mara Ortiz",
            "person_title": "People Operations",
            "job_description": (
                "Looking for an enthusiastic office coordinator and team player. "
                "Must be passionate, proactive, fast-paced, and comfortable wearing "
                "many hats while supporting a collaborative culture."
            ),
            "forensics": {
                "careers_score": 0.1,
                "status_score": 0.0,
                "github_score": 0.0,
                "eng_blog_score": 0.0,
            },
            "evidence": [],
        },
    ],
    "echodesk": [
        {
            "company_name": "Meridian Health Group",
            "company_domain": "meridianhealth.example",
            "person_name": "Dr. Priya Nair",
            "person_title": "VP of Patient Operations",
            "job_description": (
                "Hiring 4 front-desk coordinators across our clinics. Our patient "
                "call volume has doubled and average phone hold time is now over 8 "
                "minutes, driving a 22% call drop rate. Appointment scheduling "
                "backlog is causing no-shows. Experience with Twilio-based IVR and "
                "reducing wait time a strong plus."
            ),
            "forensics": {
                "careers_score": 0.82,   # 4 front-desk roles = a clear hiring pattern
                "status_score": 0.55,
                "github_score": 0.0,
                "eng_blog_score": 0.30,
            },
            "evidence": [
                {"source": "careers", "url": "https://meridianhealth.example/careers",
                 "excerpt": "Hiring 4 front-desk coordinators across our clinics", "weight": 0.25},
                {"source": "jd", "url": "https://meridianhealth.example/careers/fd-1",
                 "excerpt": "average phone hold time is now over 8 minutes, driving a 22% call drop rate",
                 "weight": 0.30},
            ],
        },
        {
            "company_name": "BrightSmile Dental Partners",
            "company_domain": "brightsmile.example",
            "person_name": "Marcus Feld",
            "person_title": "Director of Operations",
            "job_description": (
                "Seeking a patient coordinator. Our receptionist hiring can't keep "
                "up with call volume; front desk is overwhelmed and patients wait on "
                "hold. Looking to reduce appointment scheduling backlog."
            ),
            "forensics": {
                "careers_score": 0.6, "status_score": 0.0,
                "github_score": 0.0, "eng_blog_score": 0.0,
            },
            "evidence": [
                {"source": "jd", "url": "https://brightsmile.example/jobs/pc",
                 "excerpt": "front desk is overwhelmed and patients wait on hold", "weight": 0.30},
            ],
        },
        {
            "company_name": "Cloudleaf Analytics",
            "company_domain": "cloudleaf.example",
            "person_name": "Jenna Ruiz",
            "person_title": "People Ops Lead",
            "job_description": (
                "Looking for a passionate, fast-paced office administrator and team "
                "player with excellent communication to support our growing startup. "
                "Wear many hats, proactive self-starter."
            ),
            "forensics": {
                "careers_score": 0.1, "status_score": 0.0,
                "github_score": 0.0, "eng_blog_score": 0.0,
            },
            "evidence": [],
        },
    ],
    "ledgerloop": [
        {
            "company_name": "Payvault",
            "company_domain": "payvault.example",
            "person_name": "Sam Okafor",
            "person_title": "Staff Backend Engineer",
            "job_description": (
                "Backend engineer to own reliable event delivery. We're fighting "
                "duplicate webhook deliveries and need an idempotency + outbox "
                "pattern. Migrating to Kafka Connect; p99 latency and exactly once "
                "semantics matter."
            ),
            "forensics": {
                "careers_score": 0.7, "status_score": 0.6,
                "github_score": 0.75, "eng_blog_score": 0.65,
            },
            "evidence": [
                {"source": "status", "url": "https://status.payvault.example",
                 "excerpt": "Incident: duplicate charge events during webhook retry storm", "weight": 0.30},
                {"source": "github", "url": "https://github.com/payvault/webhooks/issues/214",
                 "excerpt": "Users report double-processing on retries", "weight": 0.20},
            ],
        },
        {
            "company_name": "Tinsel Commerce",
            "company_domain": "tinsel.example",
            "person_name": "Ada Berg",
            "person_title": "Eng Manager",
            "job_description": (
                "Hiring a rockstar full-stack ninja! Fast-paced, team player, "
                "passionate about clean code and stakeholder communication.",
            ),
            "forensics": {
                "careers_score": 0.1, "status_score": 0.0,
                "github_score": 0.0, "eng_blog_score": 0.0,
            },
            "evidence": [],
        },
    ],
}
