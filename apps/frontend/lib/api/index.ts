/**
 * API Module Exports
 *
 * Centralized exports for all API-related functionality.
 */

// Client utilities
export {
  API_URL,
  API_BASE,
  apiFetch,
  apiPost,
  apiPatch,
  apiPut,
  apiDelete,
  getUploadUrl,
} from './client';

// Resume operations
export {
  uploadJobDescriptions,
  improveResume,
  previewImproveResume,
  confirmImproveResume,
  fetchResume,
  fetchResumeList,
  updateResume,
  downloadResumePdf,
  deleteResume,
  type ResumeListItem,
} from './resume';

// Config operations
export {
  fetchLlmConfig,
  fetchLlmApiKey,
  updateLlmConfig,
  updateLlmApiKey,
  testLlmConnection,
  fetchSystemStatus,
  PROVIDER_INFO,
  fetchPromptConfig,
  updatePromptConfig,
  type LLMProvider,
  type LLMConfig,
  type LLMConfigUpdate,
  type DatabaseStats,
  type SystemStatus,
  type LLMHealthCheck,
  type PromptOption,
  type PromptConfig,
  type PromptConfigUpdate,
} from './config';

// Search operations
export {
  buildSearchScrapeUrl,
  fetchSearchScrape,
  generateJobDescriptionFromSearchOffer,
  getCompanyInfoFromSearchOffer,
  type OfferSource,
  type KeywordMode,
  type OfferSortBy,
  type OfferSortDirection,
  type SearchOffer,
  type SearchScraperError,
  type SearchScrapeMeta,
  type SearchScrapeResponse,
  type SearchProgressEvent,
  type SearchDoneEvent,
  type SearchScrapeParams,
  type SearchGenerateJobDescriptionRequest,
  type SearchGenerateJobDescriptionResponse,
  type SearchCompanyInfoRequest,
  type SearchCompanyInfoSourcePage,
  type SearchCompanyInfoEvidence,
  type SearchCompanyInfoStats,
  type SearchCompanyInfoResponse,
} from './search';
