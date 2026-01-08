// Class to handle breadcrumbs and their clicks with datatable refresh.
var Breadcrumbs = (function(){

    function Breadcrumbs(val){
        this.val = val;
        this.mydatatable = null;
        this.datatable_data = [];
    }

    Object.defineProperty(Breadcrumbs.prototype, "val", {
        get: function () {
            return this._val;
        },
        set: function (val) {
            this._val = val;
            this.draw();
        },
        enumerable: true,
        configurable: true
    });

    // Re-draw the breadcrumb on update of val
    Breadcrumbs.prototype.draw = function(){
        let mydiv = $("#maplink");
        mydiv.html("");

        var aTag = "   ";
        aTag += '<label id="Home" onclick="Breadcrumbs.prototype.breadcrumb_onClick(this, false);">' +
                "<span style='text-decoration: underline;cursor:pointer;color:blue;'>" +
                city.name +
                "</span></label>&nbsp;&nbsp; >> ";

        $.each(this.val, function(key,val){
            aTag += '<label id="' + val + '" onclick="Breadcrumbs.prototype.breadcrumb_onClick(this, true);">' +
                    "<span style='text-decoration: underline;cursor:pointer;color:blue;'>" +
                    val +
                    "</span></label>&nbsp;&nbsp; >> ";
        });

        mydiv.html((aTag.slice(0,aTag.length - 3)).trim());
        this.drawDatatable();
    };

    Breadcrumbs.prototype.push = function(data){
        data = data.trim();
        if (this.val.indexOf(data) < 0){
            this.val.push(data);
            this.draw();
        }
    };

    Breadcrumbs.prototype.slice = function(removeIndi){
        this.val.splice(removeIndi, 4);
        this.draw();
    };

    // Breadcrumb click event
    Breadcrumbs.prototype.breadcrumb_onClick = function(tag, flag){
        let removeIndi = 0;
        let obj_click = city;
        let original = objBreadcrumb.val.length;

        if(flag){
            let textelement = (tag.textContent).toString().trim();
            removeIndi = objBreadcrumb.val.indexOf(textelement) + 1;

            if (removeIndi < objBreadcrumb.val.length){
                objBreadcrumb.slice(removeIndi);
                obj_click = parse_data[objBreadcrumb.val[0]];

                if(objBreadcrumb.val.length == 2)
                    obj_click = obj_click.content[objBreadcrumb.val[1]];

                obj_click.obj.shape.fireEvent('click');
            }
        } else {
            objBreadcrumb.slice(removeIndi);
            obj_click.shape.click();
        }

        if(original == 3 && original > objBreadcrumb.val.length){
            $("#compochk").find("input[type=checkbox][name=grpchk]:checked").click();
            $("#compochk").html("");
            global_slum_id = 0;
            zindex = 0;
            lst_sponsor = [];
            parse_component = {};
        }
    };

    // Datatable renderer (FIXED)
    Breadcrumbs.prototype.drawDatatable = function(){
        let data = parse_data;

        try{
            $.each(this.val, function(key, value){
                data = data[value].content;
            });
        } catch(e){
            data = [];
        }

        let _this = this;
        this.datatable_data = [];

        $.each(data, function(k,v){
            _this.datatableParser(v, _this);
        });

        if(this.datatable_data.length > 0){

            this.mydatatable = $("#datatable").dataTable({
                "aaData": this.datatable_data,
                "bDestroy": true,
                "order": [[0, "asc"]],
                "search": {
                    "smart": false   // IMPORTANT: avoid fuzzy ward matches
                },
                "columns": [{
                    "title": '<div style="font-size: large;font-weight: 900;">Slums</div>',
                    "data": null,
                    "render": function (setval, type) {

                        // 🔹 SEARCH & SORT → ONLY SLUM NAME
                        if (type === "filter" || type === "sort") {
                            return setval.name;
                        }

                        // 🔹 DISPLAY → FULL HTML
                        var desc = "";
                        if (setval.legend && setval.legend.length > 0) {
                            desc = ' (' + setval.legend.join(" >> ") + ')';
                        }

                        return (
                            '<div>' +
                                '<span ' +
                                    'onclick="Breadcrumbs.prototype.datatableSlum_onClick(this);" ' +
                                    'style="font-weight:900;font-size:small;color:blue;cursor:pointer;" ' +
                                    'name="divSlum" ' +
                                    'data-slum-id="' + setval.id + '" ' +
                                    'data="' + setval.legend.join(":") + ":" + setval.name + '">' +
                                    setval.name + desc +
                                '</span>' +
                            '</div>' +
                            '<div style="font-size: small;">' + setval.info + '</div>'
                        );
                    }
                }]
            });

            $("#datatablecontainer").show();
            $("#div_legend").show();

        } else {
            $("#datatablecontainer").hide();
            $("#div_legend").hide();
        }
    };

    // Data parser for datatable
    Breadcrumbs.prototype.datatableParser = function(data_val, bread){
        if (data_val.content != undefined && data_val.content != null){
            $.each(data_val.content, function(key,val){
                bread.datatableParser(val, bread);
            });
        } else {
            bread.datatable_data.push(data_val.obj);
        }
    };

    // Datatable row click event (ID-based, correct)
    Breadcrumbs.prototype.datatableSlum_onClick = function (row){
        let slumId = $(row).attr("data-slum-id");
        let data = $(row).attr("data");
        let arr_data = data.split(":");

        objBreadcrumb.val = arr_data;

        $.each(arr_poly_disp, function(k,v){
            if (String(v.id) === String(slumId)) {
                v.shape.fire('click');
            }
        });
    };

    return Breadcrumbs;
}());
