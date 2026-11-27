document.querySelectorAll('[data-result-filters]').forEach(section => {
    const filters = [...section.querySelectorAll('[data-result-filter]')];
    const rows = [...section.querySelectorAll('[data-result-row], [data-result-group]')]
        .map(element => ({
            element,
            values: element.dataset.resultRow && JSON.parse(element.dataset.resultRow)
        }));
    const normalize = value => String(value ?? '').trim().toLowerCase();

    function apply() {
        const queries = filters.map(field => ({
            index: Number(field.dataset.resultFilter),
            value: normalize(field.value),
            exact: field.tagName === 'SELECT' || [0, 3, 4, 5].includes(Number(field.dataset.resultFilter))
        }));
        let group;
        let found = false;
        for (const { element, values } of rows) {
            if (!values) {
                group = element;
                group.hidden = true;
                continue;
            }
            const visible = queries.every(({ index, value, exact }) => {
                const actual = normalize(values[index]);
                return !value || (exact ? actual === value : actual.includes(value));
            });
            element.hidden = !visible;
            if (visible) {
                found = true;
                if (group) group.hidden = false;
            }
        }
        section.querySelector('[data-no-results]').hidden = found;
    }

    section.addEventListener('input', apply);
    section.addEventListener('change', apply);
    section.querySelector('[data-reset-result-filters]').addEventListener('click', () => {
        filters.forEach(field => { field.value = ''; });
        apply();
    });
});

