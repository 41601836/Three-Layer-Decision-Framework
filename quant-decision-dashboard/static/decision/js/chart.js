// ECharts 公共封装（深色主题）
(function(window, echarts){
    const THEME = {
        color: ['#0ea5e9','#ef4444','#22c55e','#f59e0b','#8b5cf6','#06b6d4'],
        backgroundColor: 'transparent',
        textStyle: { color: '#e2e8f0'},
        title: { textStyle: { color: '#e2e8f0'} },
        legend: { textStyle: { color: '#94a3b8'}},
        axis: { axisLine: { lineStyle: { color: '#334155'} }, axisLabel: { color: '#94a3b8' } }
    };

    function initChart(dom){
        if(!dom) return null;
        const chart = echarts.init(dom, null, {renderer: 'canvas'});
        window.addEventListener('resize', ()=> chart.resize());
        return chart;
    }

    function renderBar(dom, categories, values, opts={}){
        const chart = initChart(dom);
        if(!chart) return;
        const option = {
            backgroundColor: 'transparent',
            xAxis: { type:'category', data: categories, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color:'#94a3b8' } },
            yAxis: { type:'value', axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color:'#94a3b8' } },
            series: [{ type:'bar', data: values, itemStyle: { color: opts.color || THEME.color[0] } }],
            tooltip: { trigger:'axis' }
        };
        chart.setOption(option);
        return chart;
    }

    function renderRadar(dom, indicator, values, opts={}){
        const chart = initChart(dom);
        if(!chart) return;
        const option = {
            tooltip: {},
            radar: { indicator: indicator, name: { textStyle: { color: '#94a3b8' } }, splitLine: { lineStyle: { color: '#334155' } } },
            series: [{ type: 'radar', data: [{ value: values, name: opts.name || '得分' }], areaStyle: { opacity: 0.1 } }]
        };
        chart.setOption(option);
        return chart;
    }

    function renderPie(dom, data, opts={}){
        const chart = initChart(dom);
        if(!chart) return;
        const option = {
            tooltip: { trigger: 'item' },
            legend: { orient: 'vertical', left: 'left', textStyle: { color: '#94a3b8' } },
            series: [{ type: 'pie', radius: '60%', data: data, label: { color: '#e2e8f0' } }]
        };
        chart.setOption(option);
        return chart;
    }

    window.chartUtil = { renderBar, renderRadar, renderPie };
})(window, echarts);
