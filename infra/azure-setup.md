# Azure Infrastructure Setup — `ocupacion-busqueda-banner-rg`

This guide reproduces the exact Azure resources used by the FoundryAirlines demo, all inside a single resource group, following the [Foundry CLI quickstart](https://learn.microsoft.com/en-us/azure/foundry/tutorials/quickstart-create-foundry-resources?tabs=azurecli).

The demo runs from a **single AIServices (Foundry) account in East US 2** that hosts both the project and the two model deployments (`gpt-4.1`, `gpt-image-2`). East US 2 is used because the subscription-wide `gpt-image-2` RPM quota in Sweden Central is fully consumed by another team.

## Prerequisites

- Azure CLI **2.67+** (`az version`). Older versions miss `cognitiveservices account project` commands.
- Logged in: `az login` and `az account set --subscription <sub-id>`
- Permission to create Cognitive Services / Bing accounts and assign RBAC.

## Deployed resources

| Type | Name | Region | Notes |
|---|---|---|---|
| Resource Group | `ocupacion-busqueda-banner-rg` | eastus2 | Container for everything |
| AIServices (Foundry) | `vueling-image-8400` | eastus2 | gpt-4.1 + gpt-image-2 + Foundry projects |
| Foundry project | `vueling-image-8400/vueling-demo` | eastus2 | Hosts agents, runs, threads |
| Bing.Grounding | `vueling-bing-8400` | global | SKU G1 — provisioned for future Foundry connection |

> **Why East US 2?** `gpt-image-2` requires capacity from a subscription-wide RPM quota. In Sweden Central that quota (2 RPM) is fully consumed by another team's deployment. East US 2 has a separate quota bucket, so we deploy the consolidated account there.
>
> **Why is Bing not actively used?** See "Known issues" below — the Foundry Bing connection PUT is currently broken Azure-side, so the demo agent uses a model-knowledge fallback.

## Variables (PowerShell)

```powershell
$SUB     = "82ed1a19-7945-4aef-88ec-681c7d920d39"
$RG      = "ocupacion-busqueda-banner-rg"
$LOC     = "eastus2"
$RAND    = "8400"   # any 4-digit suffix to avoid name collisions
$FOUNDRY = "vueling-image-$RAND"   # historical name; this is now the all-in-one Foundry account
$BING    = "vueling-bing-$RAND"
$PROJECT = "vueling-demo"
$USER_OBJECT_ID = (az ad signed-in-user show --query id -o tsv)
```

## 1. Resource group

```powershell
az group create -n $RG -l $LOC
```

## 2. Foundry account (AIServices) + project

```powershell
# Account
az cognitiveservices account create `
  -n $FOUNDRY -g $RG -l $LOC `
  --kind AIServices --sku S0 `
  --custom-domain $FOUNDRY --yes

# Enable project management on the account (required before creating a project)
az resource update `
  --ids "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY" `
  --set properties.allowProjectManagement=true
```

### Create the project — **gotcha**

`az cognitiveservices account project create` and a naive ARM PUT both leave `provisioningState=Failed`. The fix is to send `identity.type=SystemAssigned` in the PUT body:

```powershell
$body = '{\"location\":\"' + $LOC + '\",\"identity\":{\"type\":\"SystemAssigned\"},\"properties\":{}}'
az rest --method put `
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY/projects/${PROJECT}?api-version=2025-06-01" `
  --body $body

# Wait ~30s, then verify
az rest --method get `
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY/projects/${PROJECT}?api-version=2025-06-01" `
  --query "properties.provisioningState" -o tsv
# Expected: Succeeded
```

## 3. Model deployments

Both models live on the same account. Tier and capacity below match the demo:

```powershell
# gpt-4.1 — Foundry agents (chat, tools, threads)
az cognitiveservices account deployment create `
  -n $FOUNDRY -g $RG `
  --deployment-name gpt-4.1 `
  --model-name gpt-4.1 --model-version "2025-04-14" --model-format OpenAI `
  --sku-name GlobalStandard --sku-capacity 50

# gpt-image-2 — banner generation
az cognitiveservices account deployment create `
  -n $FOUNDRY -g $RG `
  --deployment-name gpt-image-2 `
  --model-name gpt-image-2 --model-version "2026-04-21" --model-format OpenAI `
  --sku-name GlobalStandard --sku-capacity 1
```

> **Quota gotcha.** `gpt-image-2` has a per-subscription RPM cap (2 in many subs). If the deployment fails with `InsufficientQuota`, check usage with `az cognitiveservices usage list -l eastus2 --query "[?contains(name.value, 'Image')]" -o table` and free a slot before retrying.

## 4. Bing.Grounding resource

```powershell
az bing-search-account create `
  --name $BING -g $RG --sku G1 -l global --yes 2>$null
# Or via REST if the extension isn't available:
$bingBody = '{\"location\":\"global\",\"sku\":{\"name\":\"G1\"},\"kind\":\"Bing.Grounding\",\"properties\":{\"statisticsEnabled\":false}}'
az rest --method put `
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Bing/accounts/${BING}?api-version=2020-06-10" `
  --body $bingBody
```

## 5. RBAC — Entra ID auth from your laptop

The subscription enforces a tenant policy that sets `disableLocalAuth=true` on every new Cognitive Services account, so **API-key auth is permanently off**. The demo uses `DefaultAzureCredential` (your `az login` token). Grant your user the needed roles on the Foundry account:

```powershell
$roles = @(
  "Cognitive Services User",
  "Azure AI User",
  "Cognitive Services OpenAI User"
)
$scope = "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY"
foreach ($r in $roles) {
  az role assignment create --assignee-object-id $USER_OBJECT_ID `
    --assignee-principal-type User --role "$r" --scope $scope
}
```

After assignment, role propagation takes 1–3 min before the SDK calls succeed.

## 6. Foundry Bing-grounding connection — **known broken (Azure-side)**

The reference architecture would now create a `bing-grounding` connection inside the project, pointing at `vueling-bing-8400`. **Reproducible bug**: the PUT returns `500 ServiceError` from `https://credential.vienna-{region}.svc/credential/v1.0/.../secrets:putbatch`. We reproduced it in both **swedencentral** and **eastus2**, with multiple body shapes (with/without `metadata.ApiType=Azure`, `metadata.ResourceId`, `useWorkspaceManagedIdentity`, etc.). Inner error reveals the credential service trying to fetch a Key Vault token for a Key Vault that standalone Foundry projects don't have linked.

The intended call (kept here for when Microsoft fixes it):

```powershell
$bingId = "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Bing/accounts/$BING"
$bingKey = (az rest --method post --url "https://management.azure.com$bingId/listKeys?api-version=2020-06-10" | ConvertFrom-Json).key1
$conn = @{
  properties = @{
    category = "GroundingWithBingSearch"
    target   = "https://api.bing.microsoft.com/"
    authType = "ApiKey"
    credentials = @{ key = $bingKey }
    metadata = @{ ApiType = "Azure"; ResourceId = $bingId; location = "global" }
    isSharedToAll = $true
    useWorkspaceManagedIdentity = $false
  }
} | ConvertTo-Json -Depth 6 -Compress
$conn | Out-File -Encoding utf8 conn.json
az rest --method put `
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$FOUNDRY/projects/$PROJECT/connections/bing-grounding?api-version=2025-06-01" `
  --body "@conn.json"
# Currently returns: 500 from credential.vienna-{region}.svc — Microsoft-side regression.
```

**Workaround in code:** `app/backend/agents.py` (`_run_event_agent_sync`) tries direct Bing v7 first; on failure (the standalone Bing.Grounding key returns 401, since standalone Bing Search v7 was retired Aug 2025) it falls back to a Foundry agent that proposes a plausible recurring event for the destination/date from `gpt-4.1`'s training knowledge.

## 7. Application configuration

`app/.env` — API-key-free for Foundry/Image (Entra-only). Both endpoints point at the same account:

```dotenv
PROJECT_ENDPOINT=https://vueling-image-8400.services.ai.azure.com/api/projects/vueling-demo
MODEL_DEPLOYMENT_NAME=gpt-4.1
IMAGE_ENDPOINT=https://vueling-image-8400.openai.azure.com
IMAGE_DEPLOYMENT=gpt-image-2
IMAGE_API_VERSION=2025-04-01-preview
BING_API_KEY=<key1 of vueling-bing-8400>
BING_ENDPOINT=https://api.bing.microsoft.com/v7.0/search
```

## Smoke test

```powershell
cd app
python -c "from backend.agents import _run_event_agent_sync; print(_run_event_agent_sync({'id':1,'destination_city':'Paris','destination_country':'France','date':'2026-06-15'}))"
# Expected: a JSON dict with 'title' and 'short_description'
```

Then start the backend (`python -m uvicorn backend.main:app --port 8000`) and hit `http://127.0.0.1:8000/api/run?cached=1` to validate the SSE pipeline end-to-end without burning image credits.

## Cleanup

```powershell
az group delete -n ocupacion-busqueda-banner-rg --yes --no-wait
```
