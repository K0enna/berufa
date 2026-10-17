# Berufa

## Put your experience to work

Know what you bring. Explain it clearly. Make a career move that fits your ambitions.

Berufa is an open-source career companion for people who want more than a generic application.
Its AI Skills help you turn your experience into a clear CV, prepare thoughtful answers and
work through the questions that matter before you pursue a role.

Start with the experience you have. Get a draft you can edit and advice you can question.
Keep your own voice, your facts and the final decision.

### A better starting point for your next move

**Make your experience easier to see.** Create a complete CV or application answer from
selected text you choose to share. Refine the wording without inflating your qualifications.

**Look beyond the job title.** Discuss how a supplied role fits your experience, where it
stretches you and what you need to ask the employer before investing more time.

**Prepare answers you can stand behind.** Work on interview examples, questions and recruiter
messages grounded in what you actually know. Missing facts stay open rather than becoming claims.

### All nine Skills, one career toolkit

The [standalone Skills pack](docs/STANDALONE_SKILLS.md) supports all nine workflows with
selected pasted text, including practical briefs, assessments and complete requested drafts.
It shares the existing advice and writing methods. The legacy contract distribution remains
separate for core callers.

| Skill | Your starting point |
| --- | --- |
| Career advice | A role to consider or a career question to work through. |
| Career content | A CV, application answer, message or interview answer to draft and refine. |
| Find your direction | A career brief with your goals, constraints and first practical steps. |
| Career evidence | An organized experience summary, supplied sources and details to clarify. |
| Role evaluation | A fit assessment with relevant experience, genuine gaps and employer questions. |
| Application preparation | A focused brief and the complete application wording you request. |
| Interview preparation | A clear opening, speakable examples, honest gap answers and a call sheet. |
| Offer comparison | A comparison of supplied terms, valid pay calculations and unresolved conditions. |
| Search review | A review of confirmed events and a practical plan for your next week. |

## Try Berufa

Start with the [standalone setup guide](docs/STANDALONE_SKILLS.md). Keep the complete package
and its shared resources together. Use your assistant's documented installation and invocation
route; the guide covers Codex, Cursor and a resource-preserving Claude Code adaptation.

In Codex, begin with one result you want:

```text
$rolevane-setup: Explain the sharing boundary, then help me choose a direction,
assess a role, or write something using only the text I share.
```

After the disclosure and your agreement, paste selected experience notes and role text, then ask:

```text
$career-advice: Should I explore this role? What fits, what is missing,
and which three questions should I ask the employer?

$career-create-content: Draft a complete English CV and a concise application answer
from this selected text. Keep the facts accurate and return editable Markdown.
```

See the [completed workflow examples](plugins/rolevane-creation-fictional/examples/standalone-workflows.md)
and [acceptance report](docs/STANDALONE_ACCEPTANCE.md) for the tested scope and its limits.

Public distribution follows the **Berufa.com launch sequence**. Use the checksums and
installation guide supplied with the approved tagged release.
No Berufa account or paid Berufa plugin is required for the Skills. Your chosen AI assistant
supplies the model and may charge for usage.

## Your facts. Your choices.

Share only selected text you are entitled to share. Your configured host/model provider processes
it under your account's terms. Exclude credentials and confidential third-party details.
These nine Skills do not connect to your career workspace, fetch postings, read ambient
documents or send applications for you. The separate local core can retrieve one exact
Greenhouse posting after your local confirmation. Editable Markdown is the supported supplied-content file format.
The pack supplies no privacy sandbox or provider-retention control.

See [privacy](PRIVACY.md) and [the sharing profile](docs/STANDALONE_PROFILE.md) for details.
Never put personal career records in GitHub issues.

## For Builders And Contributors

The independent local core and its command names remain compatible with existing installations.
The [legacy contract distribution](docs/SKILL_PACK.md) preserves the earlier two selected-text
Skills and seven fictional contract guides for existing core callers; it is separate from the
nine useful standalone Skills described above.
The [technical reference](docs/REFERENCE.md) covers local setup and command contracts;
[contributing](CONTRIBUTING.md), [security](SECURITY.md) and [release instructions](docs/RELEASE.md)
cover engineering and release requirements. A technical reference is not a product launch promise.
The Skills, ChatGPT plugin and application have separate release stages; this work releases none
of them automatically.

Project-authored public source is licensed under [Apache-2.0](LICENSE), with [NOTICE](NOTICE).
