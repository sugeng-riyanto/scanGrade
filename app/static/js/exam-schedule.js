/* Keep the submitted datetime-local value as the form's source of truth. */
(function () {
    'use strict';

    function mount(picker) {
        const value = picker.querySelector('input[type="datetime-local"]');
        const controls = picker.querySelector('[data-schedule-controls]');
        const date = controls.querySelector('input[type="date"]');
        const [hour, minute, period] = controls.querySelectorAll('select');
        const clear = controls.querySelector('button');
        if (date.type !== 'date') return;

        function showValue() {
            const [day, time = '07:30'] = value.value.split('T');
            const hours = Number(time.slice(0, 2));
            date.value = day;
            hour.value = hours % 12 || 12;
            minute.value = time.slice(3, 5);
            period.value = hours >= 12 ? 'PM' : 'AM';
        }

        function updateValue() {
            const hours = Number(hour.value) % 12 + (period.value === 'PM' ? 12 : 0);
            value.value = date.value ? date.value + 'T' + String(hours).padStart(2, '0') + ':' + minute.value : '';
            value.dispatchEvent(new Event('change', { bubbles: true }));
        }

        controls.addEventListener('change', function (event) {
            event.stopPropagation();
            updateValue();
        });
        clear.addEventListener('click', function () {
            date.value = '';
            updateValue();
            showValue();
            date.focus();
        });
        value.addEventListener('schedule:refresh', showValue);
        showValue();
        value.hidden = true;
        controls.hidden = false;
    }

    document.querySelectorAll('[data-exam-schedule]').forEach(mount);
})();
