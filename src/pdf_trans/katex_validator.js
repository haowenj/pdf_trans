'use strict';

const fs = require('fs');
const path = require('path');
const katex = require(
  path.join(__dirname, 'web/static/vendor/katex/katex.min.js')
);

const VERSION = '0.18.1';
const CONFIG = {
  throwOnError: false,
  trust: false,
  maxSize: 20,
  maxExpand: 500
};

function fail(message) {
  throw new Error(message);
}

function readPayload() {
  const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (
    !payload
    || typeof payload !== 'object'
    || Array.isArray(payload)
    || Object.keys(payload).sort().join(',')
      !== 'config,formulas,version'
  ) {
    fail('invalid request object');
  }
  if (payload.version !== VERSION || katex.version !== VERSION) {
    fail('KaTeX version mismatch');
  }
  if (JSON.stringify(payload.config) !== JSON.stringify(CONFIG)) {
    fail('KaTeX config mismatch');
  }
  if (!Array.isArray(payload.formulas)) {
    fail('formulas must be an array');
  }
  const identifiers = new Set();
  for (const value of payload.formulas) {
    if (
      !value
      || typeof value !== 'object'
      || Array.isArray(value)
      || Object.keys(value).sort().join(',')
        !== 'formula,formula_id,is_block'
      || typeof value.formula_id !== 'string'
      || value.formula_id.length === 0
      || typeof value.formula !== 'string'
      || typeof value.is_block !== 'boolean'
      || identifiers.has(value.formula_id)
    ) {
      fail('invalid formula item');
    }
    identifiers.add(value.formula_id);
  }
  return payload;
}

function validate(payload) {
  return payload.formulas.map((value) => {
    katex.renderToString(value.formula, {
      ...CONFIG,
      displayMode: value.is_block
    });
    let validationError = null;
    try {
      katex.renderToString(value.formula, {
        ...CONFIG,
        displayMode: value.is_block,
        throwOnError: true
      });
    } catch (error) {
      validationError = (
        error && typeof error.message === 'string'
          ? error.message
          : 'KaTeX parse error'
      );
    }
    return {
      formula_id: value.formula_id,
      syntax_status: validationError ? 'invalid_syntax' : 'valid',
      validation_error: validationError
    };
  });
}

try {
  const payload = readPayload();
  process.stdout.write(JSON.stringify({
    version: VERSION,
    config: CONFIG,
    results: validate(payload)
  }));
} catch (error) {
  const message = (
    error && typeof error.message === 'string'
      ? error.message
      : 'unknown error'
  );
  process.stderr.write(`KaTeX batch validator failed: ${message}\n`);
  process.exitCode = 1;
}
