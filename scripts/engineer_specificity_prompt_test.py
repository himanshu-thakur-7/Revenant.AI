from agents.engineer import planner
from agents.engineer.tools import _specificity_warning


PROSPECT = {
    "company_name": "Nykaa",
    "company_domain": "nykaa.com",
    "industry": "beauty commerce",
    "contact": {"name": "Falguni Nayar", "title": "Founder and CEO"},
    "fit_rationale": (
        "High-intent beauty shoppers abandon checkout when prepaid, COD, "
        "refunds, and loyalty rewards feel disconnected."
    ),
    "pain_evidence": [
        {
            "source_url": "https://nykaa.com",
            "excerpt": "Beauty, wellness, fashion, luxury and omnichannel store experiences.",
        }
    ],
}


def test_planner_prompt_demands_account_specificity() -> None:
    prompt = planner._build_prompt(
        "Razorpay",
        "Razorpay helps Indian businesses accept payments and improve checkout.",
        "Nykaa",
        "nykaa.com",
        PROSPECT["fit_rationale"],
        "Hero headline: Your beauty, our obsession\n"
        "Visible homepage phrases: Beauty · Luxe · Stores · Offers\n"
        "Nav/category signals: Makeup · Skin · Hair · Fragrance",
        prospect_brief=PROSPECT,
    )
    system = planner._PLANNER_SYSTEM
    assert "ACCOUNT FINGERPRINT" in system
    assert "could work for a competitor by swapping" in system
    assert "merchant-specific sample data" in system
    assert "Structured prospect brief" in prompt
    assert "loyalty rewards" in prompt
    assert "Makeup" in prompt


def test_specificity_lint_warns_on_generic_html() -> None:
    html = """
    <html><body>
      <h1>Razorpay for Nykaa</h1>
      <section id="demo">
        <textarea id="demoInput">Paste your data here.</textarea>
        <button id="demoRun">Run</button>
        <div id="demoOutput">Processed successfully.</div>
      </section>
    </body></html>
    """
    warning = _specificity_warning(html, PROSPECT)
    assert "too generic" in warning
    assert "checkout" in warning or "beauty" in warning


def test_specificity_lint_accepts_account_level_html() -> None:
    html = """
    <html><body>
      <h1>Razorpay checkout orchestration for Nykaa beauty commerce</h1>
      <p>Prepaid, COD, refunds, loyalty rewards, Luxe baskets, Makeup drops,
      Skin replenishment, Hair subscriptions, Fragrance gifting, omnichannel
      store pickup, and Founder CEO reporting.</p>
      <section id="demo">
        <textarea id="demoInput">order_id=nykaa-luxe-8842
        category=Makeup, Skin, Hair
        payment_mix=UPI,COD,prepaid
        loyalty_rewards=Nykaa Prive points</textarea>
        <button id="demoRun">Optimise Nykaa checkout</button>
        <div id="demoOutput">Nykaa checkout rows: prepaid rescue, COD risk,
        refunds path, loyalty reward attach, beauty basket conversion.</div>
      </section>
    </body></html>
    """
    assert _specificity_warning(html, PROSPECT) == ""


if __name__ == "__main__":
    test_planner_prompt_demands_account_specificity()
    test_specificity_lint_warns_on_generic_html()
    test_specificity_lint_accepts_account_level_html()
    print("engineer specificity prompt/lint ok")
