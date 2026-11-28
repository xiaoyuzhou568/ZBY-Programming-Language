"""Stage 2 semantic checks: DD-024..029, DD-042, DD-047, DD-058..065.

These are executable prototype tests, not a claim of full language conformance.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from zby.interpreter import Interpreter, ZbyRuntimeError, ZbyArray, ZbyStruct, main, run
from zby.parser import parse
from zby.parser_errors import ZbySyntaxError


def execute(src: str) -> str:
    out = io.StringIO()
    run(src, stdout=out)
    return out.getvalue()


class CompositeArrayTests(unittest.TestCase):
    def test_integer_array_print(self):
        self.assertEqual(execute('let a:int[]=[1, 2, 3]; print(a);'), '[1, 2, 3]\n')

    def test_string_array_escaped(self):
        self.assertEqual(execute('let a:string[]=["a", "b\\nc"]; print(a);'), '["a", "b\\nc"]\n')

    def test_float_array(self):
        self.assertEqual(execute('let a:float[]=[1.0, 2.5]; print(a);'), '[1.0, 2.5]\n')

    def test_bool_array(self):
        self.assertEqual(execute('print([true,false]);'), '[true, false]\n')

    def test_empty_array_decl(self):
        self.assertEqual(execute('let a:int[]=[]; print(a,len(a));'), '[] 0\n')

    def test_empty_array_grouped(self):
        self.assertEqual(execute('let a:int[]=([]); print(len(a));'), '0\n')

    def test_empty_array_in_function_parameter(self):
        src = 'fn count(a:int[])->int{return len(a);} print(count([]));'
        self.assertEqual(execute(src), '0\n')

    def test_empty_array_in_function_return(self):
        self.assertEqual(execute('fn empty()->string[]{return [];} print(len(empty()));'), '0\n')

    def test_array_in_function_return(self):
        self.assertEqual(execute('fn nums()->int[]{return [1,2];} print(nums()[1]);'), '2\n')

    def test_empty_array_struct_field(self):
        src='struct Box {items:int[];} let b:Box=Box{items:[]}; print(len(b.items));'
        self.assertEqual(execute(src), '0\n')

    def test_array_no_context_empty_fails(self):
        self.assertRaisesCategory('print([]);', 'TypeError')

    def test_mixed_element_types_fails(self):
        self.assertRaisesCategory('let a:int[]=[1,2.0];', 'TypeError')

    def test_inferred_mixed_element_types_fails(self):
        self.assertRaisesCategory('print([1,"bad"]);', 'TypeError')

    def test_wrong_array_binding_type_fails(self):
        self.assertRaisesCategory('let a:string[]=[1];', 'TypeError')

    def test_array_nested_literal_fails(self):
        self.assertRaisesCategory('print([[1]]);', 'TypeError')

    def test_index_reads_last_and_first(self):
        self.assertEqual(execute('let a:int[]=[11,22]; print(a[0],a[1]);'), '11 22\n')

    def test_index_write(self):
        self.assertEqual(execute('let a:int[]=[11,22]; a[1]=33; print(a);'), '[11, 33]\n')

    def test_index_negative_fails(self):
        self.assertRaisesCategory('let a:int[]=[1]; print(a[-1]);', 'IndexError')

    def test_index_out_of_bounds_fails(self):
        self.assertRaisesCategory('let a:int[]=[1]; print(a[1]);', 'IndexError')

    def test_index_bool_fails(self):
        self.assertRaisesCategory('let a:int[]=[1]; print(a[true]);', 'TypeError')

    def test_empty_index_fails(self):
        self.assertRaisesCategory('let a:int[]=[]; print(a[0]);', 'IndexError')

    def test_index_assignment_type_fails(self):
        self.assertRaisesCategory('let a:int[]=[1]; a[0]="x";', 'TypeError')

    def test_string_index_assignment_fails(self):
        self.assertRaisesCategory('let s:string="ab"; s[0]="x";', 'TypeError')

    def test_string_unicode_codepoints(self):
        self.assertEqual(execute('print("你🙂"[1],len("é"));'), '🙂 2\n')

    def test_array_shared_assignment(self):
        self.assertEqual(execute('let a:int[]=[1]; let b:int[]=a; b[0]=9; print(a);'), '[9]\n')

    def test_array_shared_push(self):
        self.assertEqual(execute('let a:int[]=[1]; let b:int[]=a; push(b,3); print(a,len(a));'), '[1, 3] 2\n')

    def test_pop_returns_primitive(self):
        self.assertEqual(execute('let a:int[]=[1,2]; let x:int=pop(a); print(x,a);'), '2 [1]\n')

    def test_pop_empty_fails(self):
        self.assertRaisesCategory('let a:int[]=[]; pop(a);', 'ValueError')

    def test_push_wrong_element_type(self):
        self.assertRaisesCategory('let a:int[]=[]; push(a,"x");', 'TypeError')

    def test_push_first_argument_wrong_skips_second(self):
        out=io.StringIO()
        src='fn side()->int{print("NEVER");return 7;} push(1,side());'
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src, stdout=out)
        self.assertEqual(cm.exception.category, 'TypeError')
        self.assertEqual(out.getvalue(), '')

    def test_pop_wrong_arg_count_skips_side_effects(self):
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run('pop(print("NEVER"),3);',stdout=out)
        self.assertEqual(cm.exception.category, 'ArgumentError')
        self.assertEqual(out.getvalue(), '')

    def test_push_void_statement(self):
        self.assertEqual(execute('let a:int[]=[]; push(a,5); print(a);'), '[5]\n')

    def test_push_void_value_fails(self):
        self.assertRaisesCategory('let a:int[]=[]; let x:int=push(a,5);', 'TypeError')

    def test_length_valid_and_invalid(self):
        self.assertEqual(execute('print(len("你好"),len([1,2]));'), '2 2\n')
        self.assertRaisesCategory('print(len(123));','TypeError')

    def test_reassignment_rebind_no_mutation(self):
        src='let a:int[]=[1]; let b:int[]=a; b=[5]; print(a,b);'
        self.assertEqual(execute(src), '[1] [5]\n')

    def test_function_composite_argument_mutates(self):
        src='fn change(a:int[])->void{ a[0]=7; } let x:int[]=[1]; change(x); print(x);'
        self.assertEqual(execute(src), '[7]\n')

    def test_function_composite_argument_rebind_is_local(self):
        src='fn change(a:int[])->void{a=[7];} let x:int[]=[1]; change(x); print(x);'
        self.assertEqual(execute(src), '[1]\n')

    def test_function_wrong_typed_first_arg_skips_second(self):
        out=io.StringIO()
        src='fn f(a:int[], b:int)->void{} fn later()->int{print("NO"); return 1;} f(1,later());'
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src, stdout=out)
        self.assertEqual(cm.exception.category,'TypeError')
        self.assertEqual(out.getvalue(),'')

    def test_invalid_target_does_not_evaluate_rhs(self):
        src='fn side()->int{print("NEVER");return 3;} let a:int[]=[1]; a[9]=side();'
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src,stdout=out)
        self.assertEqual(cm.exception.category,'IndexError')
        self.assertEqual(out.getvalue(),'')

    def test_lhs_array_identity_preserved_across_rhs_rebind(self):
        src='''let a:int[]=[1]; let old:int[]=a;
        fn side()->int{a=[9];return 7;}
        a[0]=side(); print(old[0], a[0]);'''
        self.assertEqual(execute(src),'7 9\n')

    def test_recheck_array_length_after_rhs_pop(self):
        source='''let a:int[]=[1];
        fn erase()->int {return pop(a);}
        a[0]=erase();'''
        vm=Interpreter(stdout=io.StringIO())
        with self.assertRaises(ZbyRuntimeError) as cm:
            vm.run(parse(source))
        self.assertEqual(cm.exception.category,'IndexError')
        self.assertEqual(vm.global_scope.values['a'].value.elements,[])

    def test_old_mutation_survives_later_error(self):
        src='let a:int[]=[1]; push(a,2); print(len(a)); print(1/0);'
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError):
            run(src,stdout=out)
        self.assertEqual(out.getvalue(),'2\n')

    def test_output_reflects_post_argument_mutation(self):
        # DD-065: print is called after evaluating all arguments.
        src='let a:int[]=[1]; print(a, pop(a));'
        self.assertEqual(execute(src),'[] 1\n')

    def test_variable_type_conflict_with_struct(self):
        self.assertRaisesCategory('struct S{x:int;} let S:int=3;', 'NameError')

    def assertRaisesCategory(self, src: str, category: str):
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute(src)
        self.assertEqual(cm.exception.category, category)


class StructAndCloneTests(unittest.TestCase):
    def test_struct_source_order_independent_print(self):
        src='struct Student{name:string;age:int;} print(Student{age:20,name:"Alice"});'
        self.assertEqual(execute(src),'Student { name: "Alice", age: 20 }\n')

    def test_struct_fields_read_and_write(self):
        src='struct S{x:int;} let s:S=S{x:1}; s.x=4; print(s.x,s);'
        self.assertEqual(execute(src),'4 S { x: 4 }\n')

    def test_struct_fields_can_use_builtin_name(self):
        src='struct S{print:int;} let s:S=S{print:3}; print(s.print);'
        self.assertEqual(execute(src),'3\n')

    def test_struct_shared_alias(self):
        src='struct S{x:int;} let a:S=S{x:1}; let b:S=a; b.x=9; print(a.x);'
        self.assertEqual(execute(src),'9\n')

    def test_struct_array(self):
        src='''struct S{x:int;}
        let a:S[]=[S{x:1},S{x:2}]; a[0].x=3; print(a);'''
        self.assertEqual(execute(src),'[S { x: 3 }, S { x: 2 }]\n')

    def test_struct_array_pop_reference_shared(self):
        src='''struct S{x:int;}
        let s:S=S{x:1}; let a:S[]=[s];
        let p:S=pop(a);p.x=9;print(s.x,len(a));'''
        self.assertEqual(execute(src),'9 0\n')

    def test_struct_field_array(self):
        src='''struct Box{items:int[];}
        let b:Box=Box{items:[1,2]};b.items[0]=8;print(b);'''
        self.assertEqual(execute(src),'Box { items: [8, 2] }\n')

    def test_nested_structs(self):
        src='''struct Inner{v:int;}
        struct Outer{inner:Inner;}
        let o:Outer=Outer{inner:Inner{v:3}}; print(o.inner.v);'''
        self.assertEqual(execute(src),'3\n')

    def test_forward_struct_type_fails(self):
        self.assertFails('struct A{b:B;} struct B{x:int;}', 'NameError')

    def test_direct_recursive_struct_type_fails(self):
        self.assertFails('struct S{s:S;}', 'NameError')

    def test_indirect_recursive_struct_type_fails(self):
        self.assertFails('struct A{b:B;} struct B{a:A;}', 'NameError')

    def test_type_signature_registered_before_use(self):
        src='''fn get()->S {return S{x:3};}
        struct S{x:int;} print(get());'''
        self.assertFails(src,'NameError')

    def test_valid_struct_signature_after_definition(self):
        src='struct S{x:int;} fn get()->S{return S{x:3};} print(get());'
        self.assertEqual(execute(src),'S { x: 3 }\n')

    def test_struct_argument_reference_mutated(self):
        src='''struct S{x:int;}
        fn f(s:S)->void{s.x=42;}
        let x:S=S{x:1};f(x);print(x.x);'''
        self.assertEqual(execute(src),'42\n')

    def test_struct_return_reference_preserved(self):
        src='''struct S{x:int;}
        let x:S=S{x:1};fn f()->S{return x;}
        let y:S=f();y.x=8;print(x.x);'''
        self.assertEqual(execute(src),'8\n')

    def test_struct_missing_fields(self):
        self.assertFails('struct S{x:int;y:int;} print(S{x:1});','ValueError')

    def test_struct_extra_field(self):
        self.assertFails('struct S{x:int;} print(S{x:1,y:2});','ValueError')

    def test_struct_duplicate_initializer(self):
        self.assertFails('struct S{x:int;} print(S{x:1,x:2});','ValueError')

    def test_struct_wrong_field_type(self):
        self.assertFails('struct S{x:int;} print(S{x:"bad"});','TypeError')

    def test_struct_duplicate_declared_field(self):
        self.assertFails('struct S{x:int;x:int;}', 'NameError')

    def test_struct_field_init_source_order(self):
        src='''struct S{x:int;y:int;}
        fn one()->int{print("one");return 1;}
        fn two()->int{print("two");return 2;}
        let s:S=S{y:two(),x:one()};print(s);'''
        self.assertEqual(execute(src),'two\none\nS { x: 1, y: 2 }\n')

    def test_struct_field_access_undefined(self):
        self.assertFails('struct S{x:int;} let s:S=S{x:1}; print(s.y);', 'NameError')

    def test_struct_field_type_mismatch_assignment(self):
        self.assertFails('struct S{x:int;} let s:S=S{x:1};s.x="x";', 'TypeError')

    def test_deep_clone_array_independence(self):
        src='let a:int[]=[1];let b:int[]=clone(a);b[0]=9;print(a[0],b[0]);'
        self.assertEqual(execute(src),'1 9\n')

    def test_deep_clone_struct_independence(self):
        src='''struct S{x:int;}let a:S=S{x:1};let b:S=clone(a);
        b.x=9;print(a.x,b.x);'''
        self.assertEqual(execute(src),'1 9\n')

    def test_deep_clone_nested_struct_array(self):
        src='''struct S{items:int[];}
        let a:S=S{items:[1]};let b:S=clone(a);
        b.items[0]=9; print(a.items[0],b.items[0]);'''
        self.assertEqual(execute(src),'1 9\n')

    def test_deep_clone_preserves_internal_aliases(self):
        src='''struct Pair{left:int[];right:int[];}
        let shared:int[]=[10];
        let a:Pair=Pair{left:shared,right:shared};
        let b:Pair=clone(a);
        b.left[0]=99;
        print(b.right[0], a.left[0]);'''
        self.assertEqual(execute(src),'99 10\n')

    def test_clone_primitive_fails(self):
        self.assertFails('print(clone(1));','TypeError')

    def test_clone_wrong_arg_count_skips_effects(self):
        src='clone(print("no"), print("no"));'
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src,stdout=out)
        self.assertEqual(cm.exception.category,'ArgumentError')
        self.assertEqual(out.getvalue(),'')

    def test_struct_array_type_is_invariant(self):
        src='struct A{x:int;} struct B{x:int;} let a:A[]=[B{x:1}];'
        self.assertFails(src,'TypeError')

    def test_name_collision_function_and_struct(self):
        self.assertFails('struct A{x:int;} fn A()->void{}','NameError')

    def test_name_collision_struct_and_function(self):
        self.assertFails('fn A()->void{} struct A{x:int;}','NameError')

    def test_struct_nested_structure_legal(self):
        src='''struct Person{name:string;}
        struct Team{people:Person[];}
        let t:Team=Team{people:[Person{name:"A"}]};
        print(t.people[0].name);'''
        self.assertEqual(execute(src),'A\n')

    def test_empty_struct_remains_pending(self):
        with self.assertRaises(ZbySyntaxError):
            execute('struct Empty {}')

    def test_trailing_comma_remains_pending(self):
        with self.assertRaises(ZbySyntaxError):
            execute('let a:int[]=[1,];')

    def assertFails(self, src: str, category: str):
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute(src)
        self.assertEqual(cm.exception.category, category)


if __name__ == '__main__':
    unittest.main()

class ExtendedCompositeRegression(unittest.TestCase):
    def test_repeated_struct_element_alias_in_cloned_array(self):
        src='''struct S{x:int;}
        let item:S=S{x:1};let a:S[]=[item,item];
        let b:S[]=clone(a);b[0].x=9;print(b[1].x,item.x);'''
        self.assertEqual(execute(src),'9 1\n')

    def test_pushed_struct_shares_reference(self):
        src='''struct S{x:int;}let s:S=S{x:1};let a:S[]=[];
        push(a,s);a[0].x=6;print(s.x);'''
        self.assertEqual(execute(src),'6\n')

    def test_struct_field_value_rebind_does_not_rebind_sibling(self):
        src='''struct S{a:int[];b:int[];}
        let shared:int[]=[1];let s:S=S{a:shared,b:shared};
        s.a=[7];print(s.a,s.b,shared);'''
        self.assertEqual(execute(src),'[7] [1] [1]\n')

    def test_array_composite_equality_rejected(self):
        for src in ('print([1]==[1]);',
                    'struct S{x:int;}print(S{x:1}==S{x:1});'):
            with self.subTest(src=src), self.assertRaises(ZbyRuntimeError) as cm:
                execute(src)
            self.assertEqual(cm.exception.category,'TypeError')

    def test_type_mismatch_float_array(self):
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute('let a:float[]=[1];')
        self.assertEqual(cm.exception.category,'TypeError')

    def test_for_loop_over_array_length(self):
        src='''let a:int[]=[2,3,5];let total:int=0;
        for (let i:int=0;i<len(a);i=i+1){total=total+a[i];}
        print(total);'''
        self.assertEqual(execute(src),'10\n')

    def test_pop_primitive_return_from_function(self):
        src='''fn take(a:int[])->int{return pop(a);}
        let a:int[]=[1,9];print(take(a),a);'''
        self.assertEqual(execute(src),'9 [1]\n')

    def test_named_field_type_check_stops_later_effect(self):
        src='''struct S{x:int;y:int;}
        fn later()->int{print("NEVER");return 4;}
        let s:S=S{x:"wrong",y:later()};'''
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src,stdout=out)
        self.assertEqual(cm.exception.category,'TypeError')
        self.assertEqual(out.getvalue(),'')

    def test_print_void_stops_next_effect(self):
        src='''fn no()->void{}fn later()->int{print("NEVER");return 1;}
        print(no(),later());'''
        out=io.StringIO()
        with self.assertRaises(ZbyRuntimeError) as cm:
            run(src,stdout=out)
        self.assertEqual(cm.exception.category,'TypeError')
        self.assertEqual(out.getvalue(),'')

    def test_composite_wrong_return_type(self):
        src='''fn make()->int[]{return ["wrong"];}
        print(make());'''
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute(src)
        self.assertEqual(cm.exception.category,'TypeError')

    def test_to_string_composite_rejected(self):
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute('print(to_string([1,2]));')
        self.assertEqual(cm.exception.category,'TypeError')

    def test_cloned_empty_array_mutates_independently(self):
        src='let a:int[]=[];let b:int[]=clone(a);push(b,1);print(a,b);'
        self.assertEqual(execute(src),'[] [1]\n')

    def test_embedded_string_quotes_print(self):
        src='let a:string[]=["a\\\"b","c\\\\d"];print(a);'
        self.assertEqual(execute(src),'["a\\\"b", "c\\\\d"]\n')

    def test_nested_compound_assignment(self):
        src='''struct S{values:int[];}
        let a:S[]=[S{values:[1,2]}];a[0].values[1]=9;
        print(a[0].values);'''
        self.assertEqual(execute(src),'[1, 9]\n')

    def test_struct_scope_no_forward_function_signature(self):
        src='''fn f(a:S)->void{}struct S{x:int;}'''
        with self.assertRaises(ZbyRuntimeError) as cm:
            execute(src)
        self.assertEqual(cm.exception.category,'NameError')
