# Data-processing record — Software Factory provider (F-13, owner step 4)

**Decision (2026-11-11, owner Anthony):** for now, only the owner's own
projects go through the Software Factory — JobFinderOS first, then
SoloAdventurer. JobFinderOS is an owner repo (`data_owner: self`,
implicit in `.factory/project.yaml`), so processing by the factory's
model provider **Z.ai** (GLM Coding Plan) is allowed: factory sessions
process this repo's source and planning docs via Z.ai inside sandboxed
sessions, with egress confined to the profile's allowlists (`api.z.ai`
is this project's own inference host, F-59). No client data exists in
this repository.

**Before any client repo runs through the factory**, two things must be
recorded per repo, in writing, before any session starts:

1. a data processing agreement with the provider **or** the client's
   written consent to the named processor (for an EU client: DPA/SCC
   posture resolved), recorded in that repo's equivalent of this file;
2. an explicit **non-Z.ai provider block** in that repo's
   `project.yaml` — `data_owner: client` with `kind: zai` refuses at
   load (F-59 AC-1), so the restriction is enforced, not advisory.

Until both exist for a repo, that repo does not go through the factory.
