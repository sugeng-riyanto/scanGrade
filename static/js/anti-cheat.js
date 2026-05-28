/** Anti-Cheat Module - Tab Switch Detection */
(function() {
    'use strict';

    const DEBOUNCE_MS = 1500;
    const BLUR_IGNORE_MS = 500;
    const API_ENDPOINT = '/api/violation/log';
    const EXAM_ID = document.querySelector('[data-exam-id]')?.dataset?.examId || '';

    let lastBlurTime = 0;
    let pendingViolations = [];
    let isSubmitting = false;

    function logViolation(type, metadata) {
        if (isSubmitting) return;
        const now = Date.now();

        // Ignore blur < 500ms (false positive mitigation)
        if (type === 'blur' && (now - lastBlurTime) < BLUR_IGNORE_MS) {
            return;
        }
        lastBlurTime = now;

        pendingViolations.push({
            exam_id: EXAM_ID,
            violation_type: type,
            timestamp: Math.floor(now / 1000),
            metadata: metadata || {},
        });

        // Send batch with debounce
        clearTimeout(window.__violationDebounce);
        window.__violationDebounce = setTimeout(sendPending, DEBOUNCE_MS);
    }

    function sendPending() {
        if (pendingViolations.length === 0) return;

        const payload = pendingViolations.slice();
        pendingViolations = [];

        // Use sendBeacon for reliability (works even on tab close)
        const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
        navigator.sendBeacon(API_ENDPOINT, blob);

        // Fallback to fetch if sendBeacon fails
        fetch(API_ENDPOINT, {
            method: 'POST',
            body: JSON.stringify(payload),
            headers: { 'Content-Type': 'application/json' },
            keepalive: true,
        }).catch(function() {});
    }

    // Visibility change detection (tab switch)
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            logViolation('tab_hidden', {});
        } else {
            logViolation('tab_visible', {
                duration_away: Math.floor((Date.now() - lastBlurTime) / 1000),
            });
        }
    });

    // Window blur/focus (alt+tab, window switch)
    window.addEventListener('blur', function() {
        logViolation('blur', {});
    });

    window.addEventListener('focus', function() {
        // Re-check visibility
    });

    // Page unload - send all pending
    window.addEventListener('beforeunload', function() {
        sendPending();
    });

    // Submit exam - mark as submitting to stop violation logs
    window.markExamSubmitted = function() {
        isSubmitting = true;
        sendPending();
    };

    console.log('[ScanGrade Anti-Cheat] Initialized');
})();
