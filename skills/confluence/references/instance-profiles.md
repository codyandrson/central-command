# Instance profiles: what macros actually exist, per instance

Confluence has no universal macro set. What renders on one instance is an
"unknown macro" placeholder on another — installed marketplace apps differ,
and even built-ins vary by edition and version. `CC_CONFLUENCE_PROFILE`
names which allowlist is live for the instance you're currently talking to.
**A macro outside the active profile is a capability gap to `declare_gap` on
— never a guess to try and see what happens**, because a wrong guess doesn't
error, it just sits broken on the page (`storage-format`).

## Profiles are per instance

A profile is a named allowlist of the macros one instance really has. The
profile names the deployment ships, and each one's macro inventory (the
instance's edition, its installed marketplace apps, which categories exist),
are instance facts: they are read from the instance overlay, not from this
file.

<!-- overlay: confluence_profile_cloud — moved to the instance overlay by epic-instance-facts-relocation -->
<!-- overlay: confluence_profile_server — moved to the instance overlay by epic-instance-facts-relocation -->

What does hold for every instance of a flavor, and stays here as generic
guidance:

- **Cloud, free plan:** the built-in Cloud macro set (panels, Table of
  Contents, Expand, Status, Children Display, Page Properties + Report,
  Excerpt/Excerpt Include, Code Block, layout macros, Attachments, Labels
  List, the Jira macro once an application link exists) plus whichever
  marketplace apps the site installed. A free plan does not carry paid apps
  (Table Filter, Forms, Scroll export, test-management reports), and Advanced
  Roadmaps embeds need a paid plan tier.
- **Server/Data Center:** the built-ins above plus the large marketplace
  surface that edition's site has installed. Every Jira and gadget embed
  needs the Confluence↔Jira application link already configured — an
  empty or erroring embed may mean the link is missing, not that the macro
  syntax is wrong. An instance's macro browser can list a macro its own
  description marks deprecated (a legacy draw.io macro is the usual case):
  prefer the current macro it names.

## Which families need discovery, not a recipe

Layout, panels & callouts, and the "core built-ins" line above have verified
storage-format recipes in `storage-format.md` — parameter names sourced from
Atlassian's own macro docs. **Tables & data apps, Forms, Scroll export,
test-management reports, BibTeX/LaTeX/Markdown, and Multiexcerpt are
discovery-first**: their parameter surface is app-version-specific (or, for
Multiexcerpt, ambiguous between two similarly-named products) and not
something this skill can hand you a trustworthy recipe for. Find a real
example on the instance and copy it — `macro-discovery.md` has the procedure
and the full macro-to-doctrine mapping. Jira & gadget embeds have verified
skeletons but a separate live dependency (the application link) that no
amount of correct XML substitutes for.

## The rule

`CC_CONFLUENCE_PROFILE` names the active profile. Compose a macro body only
from that profile's allowlist. A macro that would be useful but isn't listed
for the active instance is a capability gap — `declare_gap`, name the macro
and the need, and let the operator decide whether to install the app or
choose a substitute. Do not try it "to see" — the failure mode is a broken
placeholder on a page someone else may already be reading, not a clean
error.
