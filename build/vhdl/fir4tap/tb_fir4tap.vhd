-- Auto-generated testbench for FIR4Tap
-- Generated from Python behavioral simulation (BehaviorExecutor).
-- Run with: ghdl -a --std=08 fixed_point_pkg.vhd fir4tap.vhd tb_fir4tap.vhd
--           ghdl -e --std=08 tb_fir4tap
--           ghdl -r --std=08 tb_fir4tap

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
library work;
use work.fixed_point_pkg.all;

entity tb_fir4tap is
end entity tb_fir4tap;

architecture sim of tb_fir4tap is

    -- DUT ports
    signal x0           : signed(15 downto 0) := (others => '0');
    signal x1           : signed(15 downto 0) := (others => '0');
    signal x2           : signed(15 downto 0) := (others => '0');
    signal x3           : signed(15 downto 0) := (others => '0');
    signal y            : signed(16 downto 0);

    -- Format constants for real_to_sfp / sfp_to_real
    constant FMT_X0       : fp_format_t := (1, 15, true, 0.0);
    constant FMT_X1       : fp_format_t := (1, 15, true, 0.0);
    constant FMT_X2       : fp_format_t := (1, 15, true, 0.0);
    constant FMT_X3       : fp_format_t := (1, 15, true, 0.0);
    constant FMT_Y        : fp_format_t := (2, 15, true, 0.0);

begin

    -- DUT instantiation
    dut: entity work.fir4tap
        port map (
            x0 => x0,
            x1 => x1,
            x2 => x2,
            x3 => x3,
            y => y
        );

    stim: process
        variable expected : integer;
        variable actual   : integer;
        variable err      : integer;
        variable pass     : boolean := true;
    begin

        -- impulse_c0
        x0 <= real_to_sfp(0.9999694824, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 8191;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c0: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c1
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.9999694824, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 8191;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c1: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c2
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.9999694824, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 8191;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c2: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c3
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.9999694824, FMT_X3);
        wait for 10 ns;
        expected := 8191;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c3: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c4
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 0;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c4: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c5
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 0;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c5: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c6
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 0;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c6: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- impulse_c7
        x0 <= real_to_sfp(0.0000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 0;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "impulse_c7: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c0
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.0000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 4096;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c0: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c1
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.0000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 8192;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c1: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c2
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.0000000000, FMT_X3);
        wait for 10 ns;
        expected := 12288;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c2: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c3
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.5000000000, FMT_X3);
        wait for 10 ns;
        expected := 16384;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c3: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c4
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.5000000000, FMT_X3);
        wait for 10 ns;
        expected := 16384;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c4: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c5
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.5000000000, FMT_X3);
        wait for 10 ns;
        expected := 16384;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c5: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c6
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.5000000000, FMT_X3);
        wait for 10 ns;
        expected := 16384;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c6: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        -- dc_half_c7
        x0 <= real_to_sfp(0.5000000000, FMT_X0);
        x1 <= real_to_sfp(0.5000000000, FMT_X1);
        x2 <= real_to_sfp(0.5000000000, FMT_X2);
        x3 <= real_to_sfp(0.5000000000, FMT_X3);
        wait for 10 ns;
        expected := 16384;
        actual   := to_integer(y);
        err      := actual - expected;
        if err < 0 then err := -err; end if;
        assert err <= 1
            report "dc_half_c7: y mismatch got " & integer'image(actual) & " expected " & integer'image(expected)
            severity error;
        if err > 1 then pass := false; end if;

        if pass then
            report "SIMPASS: all 16 fir4tap test cases passed" severity note;
        else
            report "SIMFAIL: one or more fir4tap test cases failed" severity failure;
        end if;
        wait;
    end process stim;

end architecture sim;
