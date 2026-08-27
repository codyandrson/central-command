(function ($, hljs) {
    'use strict';

    function formatJsonExamples() {
        function formatJson(codeElem) {
            try {
                var obj = JSON.parse(codeElem.innerHTML);
                codeElem.innerHTML = JSON.stringify(obj, null, 4);
            }
            catch (e) {
                // err
            }
        }

        var jsonCodes = document.getElementsByTagName('code');
        for (var i = 0; i < jsonCodes.length; i++) {
            formatJson(jsonCodes[i]);
        }

        hljs.configure({
            languages: ['json']
        });

        $('.representation-doc-block pre code, pre code.json').each(function (i, block) {
            hljs.highlightBlock(block);
        });
    }

    function fixWadlHtml() {
        // removing empty paragraphs
        $('.representation-doc p:empty').remove();
        //
        // //move examples at the and of the representation's docs
        $('.representation-doc').each(function () {
            var doc = $(this);
            var children = doc.children();
            doc.children().remove();
            doc.append(children);
        });

    }

    function openMethodSpecifiedInTheUrl() {
        if (window.location.hash) {
            // jquery have trouble with parsing special characters
            var $header = $(document.getElementById(window.location.hash.substr(1)));
            if ($header.hasClass('expandable')) {
                showMethod($header);
            }
        }
    }

    function changeUrl(link) {
        if (history.pushState) {
            history.pushState(null, null, link);
        }
        else {
            location.hash = link;
        }
    }

    function hideMethod($header) {
        var $body = $header.next();
        $body.hide();
        $header.removeClass('expanded');
    }

    function showMethod($header) {
        var $body = $header.next();
        $body.show();
        $header.addClass('expanded');
    }

    function makeMethodExpandable() {
        function toggleMethod($header) {
            if ($header.hasClass('expanded')) {
                hideMethod($header);
            }
            else {
                showMethod($header);
                changeUrl($('a', $header).attr('href'));
            }
        }

        $('h4.expandable .left').on('click', function () {
            toggleMethod($(this).parent());
            return false;
        });

        $('h4.expandable a').on('click', function () {
            toggleMethod($(this).parent().parent());
            return false;
        });

        var expandAllText = 'Expand all methods';
        var hideAllText = 'Hide all methods';

        var $expandMethods = $('h3 .expand-methods');
        $expandMethods.text(expandAllText);
        $expandMethods.on('click', function () {
            var $button = $(this);
            var $headerParent = $button.parent().parent();
            if ($button.hasClass('expanded')) {
                $button.text(expandAllText);
                $button.removeClass('expanded');
                $('h4.expandable', $headerParent).each(function () {
                    hideMethod($(this));
                });
            }
            else {
                $button.text(hideAllText);
                $button.addClass('expanded');
                $('h4.expandable', $headerParent).each(function () {
                    showMethod($(this));
                });
            }
        });

        var $allExpandable = $('h4.expandable');
        var $expandAll = $('#expand-all');
        $expandAll.text(expandAllText);
        $expandAll.on('click', function () {
            var $button = $(this);
            if ($button.hasClass('expanded')) {
                $button.text(expandAllText);
                $button.removeClass('expanded');
                $expandMethods.each(function () {
                    var $bt = $(this);
                    $bt.text(expandAllText);
                    $bt.removeClass('expanded');
                });
                $allExpandable.each(function () {
                    hideMethod($(this));
                });
            }
            else {
                $button.text(hideAllText);
                $button.addClass('expanded');
                $expandMethods.each(function () {
                    var $bt = $(this);
                    $bt.text(hideAllText);
                    $bt.addClass('expanded');
                });
                $allExpandable.each(function () {
                    showMethod($(this));
                });
            }
        });
    }

    function makeSchemaExpandable() {
        // identify schemas
        $('.representation-doc-block pre').each(function () {
            var $pre = $(this);
            var $block = $pre.parent();
            if ($('h6', $block).text().toLowerCase() === 'schema') {
                $block.addClass('schema');
            }
        });

        var $schemas = $('.schema h6');
        $schemas.each(function () {
            var $header = $(this);
            $header.append('<button class="expand"></button>');
        });

        $schemas.on('click', function () {
            var $header = $(this);
            var $pre = $('pre', $header.parent());
            if ($header.hasClass('expanded')) {
                $pre.hide();
                $header.removeClass('expanded');
            }
            else {
                $pre.show();
                $header.addClass('expanded');
            }
        });
    }

    $(function () { // eslint-disable-line @atlassian/onready-checks/no-jquery-onready
        formatJsonExamples();
        fixWadlHtml();
        openMethodSpecifiedInTheUrl();
        makeMethodExpandable();
        makeSchemaExpandable();
    });
})(window.jQuery, window.hljs);
