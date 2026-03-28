create or replace package body myschema.mypackage as

    vNum number;

    procedure test_procedure(pName in varchar2, pVal in number) is
        vRes number;
        vUpdated date;
    begin

        --package4.proc2(pName, pVAl);
        vRes := myschema.package2.proc2(pName, pVAl);
        

        begin
            for i in (
                select updated 
                into vUpdated 
                from myschema.table2 t, 
                    table2 b 
                where t.id = vRes 
                and b.id = t.bid) 
            loop

                vRes:= 100;
                if vRes < 3 then
                    myschema2.package2.proc3(pName, pVAl);
                elsif vRes < 300 then
                    myschema3.package4.proc4(pName, pVAl);
                else
                    myschema3.package4.proc4(pName, 999);
                end if;
                vres := 200;

            end loop;

            for i in 1 .. xx.count loop
                dbms_output.put_line('xxx');
            end loop;

            select 1 into vRes from dual;
            
            begin
              vRes := null;
            end;
        exception
            when no_data_found then
            package3.log('error while call test_procedure, pName: ' || pName);
            raise;
            when others then
            raise;
        end;
    end;