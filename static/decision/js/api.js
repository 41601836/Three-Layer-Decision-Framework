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
        }
    };
})(window, jQuery);
