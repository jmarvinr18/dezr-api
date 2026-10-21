"""The site settings as shipped (same values as dezr-ui/shared/site/defaults.ts).

A section an admin has never saved shows these; resetting a section brings them back. Keys
are snake_case here and camelCase on the wire (see app/schema/site_settings.py).
"""

SITE_DEFAULTS = {
    "site": {
        "name": "Donato & Zarate Law Firm",
        "logo": {
            "title": "Donato & Zarate",
            "tagline": "Attorneys and Counselors at Law",
        },
        "description": "General-practice law office in Makati City, Metro Manila, Philippines.",
        "url": "https://www.deszr.com",
    },
    "contact": {
        "address_lines": [
            "7/F Electra House, 115 Esteban Street, Legaspi Village",
            "1229 Makati City, Philippines",
        ],
        "email": "admin@deszr.com",
        "japan_desk_email": "japandesk@deszr.com",
        "phone": "+632-8-892-7288",
        "fax": "+632-8-893-2764",
        "hours": "Monday - Friday, 8.00 AM - 5.00 PM (GMT+8)",
    },
    "nav": [
        {"label": "Home", "to": "/", "home": True},
        {"label": "Partners and Counsel", "to": "/partners-and-counsel", "home": False},
        {"label": "Library", "to": "/library", "home": False},
        {"label": "Deutsch", "to": "/deutsch", "home": False},
        {"label": "Nihongo", "to": "/nihongo", "home": False},
        {"label": "Tagalog", "to": "/tagalog", "home": False},
        {"label": "Contact", "to": "/contact", "home": False},
        {"label": "Assistant", "to": "/assistant", "home": False},
    ],
    # Slide art is drawn in SVG by the UI (SkylineArt.vue); `scene` picks the palette.
    "slides": [
        {"title": "Corporation", "caption": "Incorporation, Joint Ventures, Mergers & Acquisitions, Corporate Compliance", "scene": "dusk", "headline": "Counsel", "headline2": "Clarity", "tagline": "Practical advice for the companies and people we serve."},
        {"title": "Banking and Finance", "caption": "Lending, Securities, Due Diligence, Foreign Exchange, Regulation", "scene": "night", "headline": "Capital", "headline2": "Confidence", "tagline": "Structuring finance deals that hold up."},
        {"title": "Taxation", "caption": "Income Tax, VAT, Withholding Tax, Customs Duties", "scene": "teal", "headline": "Planning", "headline2": "Compliance", "tagline": "Tax advice built into every transaction."},
        {"title": "Intellectual Property", "caption": "Patents, Trademarks, Copyrights, Technology Transfers", "scene": "violet", "headline": "Ideas", "headline2": "Protection", "tagline": "Registering, licensing and enforcing your rights."},
        {"title": "Immigration", "caption": "Work Visas, Investor Visas, Retiree Visas, Employment Permits", "scene": "dawn", "headline": "Arrival", "headline2": "Residence", "tagline": "Visas and permits for people moving to the Philippines."},
        {"title": "Employment", "caption": "Contracting, Hiring, Termination, Collective Bargaining, Social Security", "scene": "night", "headline": "People", "headline2": "Policy", "tagline": "Sound labor practices for employers."},
        {"title": "Dispute Resolution", "caption": "Litigation, Administrative Cases, Mediation, Arbitration", "scene": "dusk", "headline": "Advocacy", "headline2": "Resolution", "tagline": "In court, before agencies, and at the table."},
        {"title": "Trade & Commerce", "caption": "Supply Contracts, Service Contracts, Franchise Agreements", "scene": "teal", "headline": "Trade", "headline2": "Growth", "tagline": "Contracts that keep business moving."},
    ],
    "mission": {
        "title": "Our Mission",
        "text": "Clear, dependable counsel for businesses and individuals in the Philippines.",
        "link": {"label": "More info", "to": "/partners-and-counsel"},
    },
    # Download banners in the right column. Upload the PDFs in the admin area's media library.
    "banners": [
        {"variant": "en", "kicker": "", "title": "Doing Business", "title2": "in the Philippines", "action": "Download", "url": "/downloads/doing-business-in-the-philippines.pdf", "lang": "en"},
        {"variant": "ja", "kicker": "", "title": "フィリピンでのビジネスの手引き", "title2": "", "action": "ダウンロード", "url": "/downloads/doing-business-in-the-philippines-ja.pdf", "lang": "ja"},
        {"variant": "de", "kicker": "", "title": "Geschäftstätigkeit", "title2": "auf den Philippinen", "action": "Download", "url": "/downloads/doing-business-in-the-philippines-de.pdf", "lang": "de"},
        {"variant": "fi", "kicker": "Foreign Investments in the", "title": "Philippines", "title2": "", "action": "", "url": "/downloads/foreign-investments-in-the-philippines.pdf", "lang": "en"},
    ],
    "footer": {
        "columns": [
            {
                "title": "About Us",
                "links": [
                    {"label": "Library", "to": "/library"},
                    {"label": "Practice", "to": "/"},
                    {"label": "Partners and Counsel", "to": "/partners-and-counsel"},
                    {"label": "Work with us", "to": "/work-with-us"},
                    {"label": "AI Assistant", "to": "/assistant"},
                ],
            },
            {
                "title": "Footer Menu",
                "links": [{"label": "Site Map", "to": "/site-map"}],
            },
        ],
    },
    # AI assistant wording (full page at /assistant and the chat bubble on every other page).
    "assistant": {
        "title": "AI Assistant",
        "name": "Firm Assistant",
        "subtitle": "Ask about our practice areas, doing business in the Philippines, or how to reach the firm.",
        "greeting": "How can I help?",
        "intro": "I can answer general questions about the firm and point you to the right practice area.",
        "placeholder": "Ask a question…",
        "prompts": [
            "What practice areas does the firm cover?",
            "How do I set up a company in the Philippines?",
            "What visas are available for foreign investors?",
            "How can I book a consultation?",
        ],
        "disclaimer": "AI can make mistakes. Answers are general information, not legal advice.",
    },
}
