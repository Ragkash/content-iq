// ============================================================
// Content IQ — Azure Infrastructure
// Deploy all required resources to a new subscription/account
// ============================================================

targetScope = 'resourceGroup'

// ── Parameters ──────────────────────────────────────────────
@description('Short environment tag, e.g. dev / staging / prod')
param environmentName string = 'dev'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Prefix applied to every resource name')
param resourcePrefix string = 'contentiq'

@description('Azure AI Search SKU — must be S1 or higher for BinaryQuantization')
@allowed(['standard', 'standard2', 'standard3'])
param searchSku string = 'standard'

@description('Storage account SKU')
@allowed(['Standard_LRS', 'Standard_GRS', 'Standard_ZRS'])
param storageSku string = 'Standard_LRS'

@description('Name of the raw-documents blob container')
param rawContainerName string = 'container'

@description('Name of the processed-documents blob container')
param processedContainerName string = 'processed'

// ── Variables ────────────────────────────────────────────────
var suffix = '${resourcePrefix}-${environmentName}'
var openAiName = '${suffix}-oai'
var searchName = '${suffix}-search'
var storageAccountName = replace('${resourcePrefix}${environmentName}store', '-', '')
var aiServicesName = '${suffix}-aiservices'

// ── Azure Storage Account ────────────────────────────────────
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: take(storageAccountName, 24) // storage names max 24 chars, lowercase
  location: location
  sku: {
    name: storageSku
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource rawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: rawContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource processedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: processedContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ── Azure AI Services (OpenAI + Content Understanding) ───────
// A single AI Services account gives you both OpenAI and Content Understanding endpoints
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: aiServicesName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiServicesName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// ── OpenAI Model Deployments ──────────────────────────────────
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: aiServices
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 10 // TPM in thousands — raise if you have quota
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
  }
}

resource adaDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: aiServices
  name: 'text-embedding-ada-002'
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-ada-002'
      version: '2'
    }
  }
  dependsOn: [gpt4oDeployment] // deployments must be sequential
}

// ── Azure AI Search ───────────────────────────────────────────
resource searchService 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: searchName
  location: location
  sku: {
    name: searchSku // S1+ required for BinaryQuantization
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    semanticSearch: 'standard' // enables semantic ranker
  }
}

// ── Outputs (used by deploy.sh to build .env) ────────────────
output storageAccountName string = storageAccount.name
output storageConnectionString string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
output openAiEndpoint string = aiServices.properties.endpoint
output openAiKey string = aiServices.listKeys().key1
output contentUnderstandingEndpoint string = 'https://${aiServicesName}.services.ai.azure.com/'
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output searchKey string = searchService.listAdminKeys().primaryKey
