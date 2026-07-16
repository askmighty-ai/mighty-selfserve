// Mighty Worker service-worker entry point.
// Load the existing worker first, then install narrowly-scoped silent strategies.
importScripts(
  'background.js',
  'amex_silent_verification.js',
  'amex_invisible_context_test.js',
);
