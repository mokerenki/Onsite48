// frontend/src/data/connectors.ts

export interface Connector {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  category: 'productivity' | 'marketing' | 'sales' | 'development' | 'analytics' | 'social' | 'ai';
  authType: 'oauth2' | 'api_key' | 'none';
  mcpServer?: string;  // MCP server name from config.yaml
  tools: string[];     // Tools this connector provides
}

export const CONNECTORS: Connector[] = [
  // ─── Productivity ──────────────────────────────────────────────
  {
    id: 'outlook',
    name: 'Outlook Mail',
    description: 'Write, search, and manage your Outlook emails seamlessly within Onsite48AI',
    icon: 'outlook',
    color: '#0078D4',
    category: 'productivity',
    authType: 'oauth2',
    mcpServer: 'outlook',
    tools: ['outlook_send_email', 'outlook_list_emails', 'outlook_search_emails'],
  },
  {
    id: 'gmail',
    name: 'Gmail',
    description: 'Send, search, and manage your Gmail emails from within Onsite48AI',
    icon: 'gmail',
    color: '#EA4335',
    category: 'productivity',
    authType: 'oauth2',
    mcpServer: 'gmail',
    tools: ['gmail_send_email', 'gmail_list_emails', 'gmail_search_emails'],
  },
  {
    id: 'slack',
    name: 'Slack',
    description: 'Send messages, manage channels, and automate workflows in Slack',
    icon: 'slack',
    color: '#4A154B',
    category: 'productivity',
    authType: 'oauth2',
    mcpServer: 'slack',
    tools: ['slack_send_message', 'slack_list_channels', 'slack_search_messages'],
  },
  {
    id: 'notion',
    name: 'Notion',
    description: 'Search workspace content, update notes, and automate workflows in Notion',
    icon: 'notion',
    color: '#000000',
    category: 'productivity',
    authType: 'oauth2',
    mcpServer: 'notion',
    tools: ['notion_search', 'notion_update_page', 'notion_create_page'],
  },

  // ─── Marketing ──────────────────────────────────────────────────
  {
    id: 'meta-ads',
    name: 'Meta Ads Manager',
    description: 'Automate ads insights and optimization to save hours and maximize profits',
    icon: 'facebook',
    color: '#1877F2',
    category: 'marketing',
    authType: 'oauth2',
    mcpServer: 'meta-ads',
    tools: ['meta_get_campaigns', 'meta_get_insights', 'meta_optimize_budget'],
  },
  {
    id: 'instagram-creators',
    name: 'Instagram Creator Marketplace',
    description: 'Discover creators that fit your brand\'s reach, topics, and style',
    icon: 'instagram',
    color: '#E4405F',
    category: 'marketing',
    authType: 'oauth2',
    mcpServer: 'instagram',
    tools: ['instagram_search_creators', 'instagram_analyze_profile'],
  },
  {
    id: 'brand24',
    name: 'Brand24',
    description: 'Monitor brand mentions, sentiment, and influencers',
    icon: 'brand24',
    color: '#FF6B00',
    category: 'marketing',
    authType: 'api_key',
    mcpServer: 'brand24',
    tools: ['brand24_get_mentions', 'brand24_get_sentiment'],
  },

  // ─── Sales ──────────────────────────────────────────────────────
  {
    id: 'salesforce',
    name: 'Salesforce',
    description: 'Manage leads, opportunities, and automate CRM workflows',
    icon: 'salesforce',
    color: '#00A1E0',
    category: 'sales',
    authType: 'oauth2',
    mcpServer: 'salesforce',
    tools: ['sfdc_query', 'sfdc_create_lead', 'sfdc_update_opportunity'],
  },

  // ─── Development ────────────────────────────────────────────────
  {
    id: 'github',
    name: 'GitHub',
    description: 'Manage repositories, create issues, and automate code workflows',
    icon: 'github',
    color: '#181717',
    category: 'development',
    authType: 'oauth2',
    mcpServer: 'github',
    tools: ['github_create_repo', 'github_create_issue', 'github_search_code'],
  },

  // ─── AI ──────────────────────────────────────────────────────────
  {
    id: 'elevenlabs',
    name: 'ElevenLabs API',
    description: 'Generate realistic voices, clone speech, and create custom audio content',
    icon: 'elevenlabs',
    color: '#7B4DFF',
    category: 'ai',
    authType: 'api_key',
    mcpServer: 'elevenlabs',
    tools: ['elevenlabs_generate_audio', 'elevenlabs_clone_voice'],
  },

  // ─── Analytics ──────────────────────────────────────────────────
  {
    id: 'similarweb',
    name: 'Similarweb',
    description: 'Access website traffic, audience, SEO, and app intelligence',
    icon: 'similarweb',
    color: '#1F7D8A',
    category: 'analytics',
    authType: 'api_key',
    mcpServer: 'similarweb',
    tools: ['similarweb_get_traffic', 'similarweb_get_audience'],
  },

  {
    id: 'my-browser',
    name: 'My Browser',
    description: 'Use your local Chrome with existing logins. Pair once, authorize per task; agent works in a task-named tab group you can watch, take over, or stop.',
    icon: 'chrome',
    color: '#4285F4',
    category: 'productivity',
    authType: 'none',
    tools: ['browser_observe', 'browser_act', 'browser_takeover', 'browser_stop'],
  },
];