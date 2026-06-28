// 全局 API 封装（基于 jQuery）
// 所有接口前缀统一为 /api/decision/
(function(window, $){
    const PREFIX = '/api/decision/';

    function handleFail(xhr, status, err, cb){
        console.error('API request failed', status, err);
        try{ window.showTip && window.showTip('接口请求失败：' + (err || status), 'error'); }catch(e){}
        if(typeof cb === 'function') cb({code: -1, msg: '请求失败', data: null});
    }

    function get(path, cb){
        const url = PREFIX + path;
        return $.ajax({
            method: 'GET',
            url: url,
            dataType: 'json',
            timeout: 20000,
        }).done(function(res){
            if(typeof cb === 'function') cb(res);
        }).fail(function(xhr, status, err){
            handleFail(xhr, status, err, cb);
        });
    }

    function post(path, data, cb){
        const url = PREFIX + path;
        return $.ajax({
            method: 'POST',
            url: url,
            data: JSON.stringify(data),
            contentType: 'application/json',
            dataType: 'json',
            timeout: 60000, // 回测时间可能稍长，超时设为 60s
        }).done(function(res){
            if(typeof cb === 'function') cb(res);
        }).fail(function(xhr, status, err){
            handleFail(xhr, status, err, cb);
        });
    }

    window.api = {
        // 触发并获取全流程执行结果
        runTotal: function(cb){
            return get('run_total', cb);
        },
        // 获取宏观数据
        getMacro: function(cb){
            return get('get_macro', cb);
        },
        // 获取板块数据
        getBoard: function(cb){
            return get('get_board', cb);
        },
        // 获取个股数据
        getStock: function(cb){
            return get('get_stock', cb);
        },
        // 一键运行机器学习选股
        runMLScan: function(cb){
            return post('ml_scan', {}, cb);
        },
        // 一键运行机器学习回测
        runMLBacktest: function(params, cb){
            return post('backtest_ml', params, cb);
        },
        // 获取个股多维度诊断分析
        getStockAnalysis: function(tsCode, cb){
            return post('stock_analysis', { ts_code: tsCode }, cb);
        },
        // 获取个股 ML 集成模型历史信号与评分
        getMLSignal: function(tsCode, cb){
            return get('ml_signal?ts_code=' + tsCode, cb);
        }
    };
})(window, jQuery);
