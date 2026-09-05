#include <bits/stdc++.h>
#define ll long long
using namespace std;

int main() {
    cin.tie(nullptr)->sync_with_stdio(false);
    int t;
    cin >> t;
    while(t --){
        int n;
        ll s1, s2;
        cin >> n >> s1 >> s2;
        vector<pair<int, int>> r(n);
        for(int i = 0; i < n; i ++){
            cin >> r[i].first;
            r[i].second = i;
        }
        sort(r.begin(), r.end(), greater<pair<int, int>>());

        vector<int> a, b;
        for(int i = 0; i < n; i ++){
            if(s1 * ((int)a.size() + 1) < s2 * ((int)b.size() + 1))
                a.emplace_back(r[i].second + 1);
            else
                b.emplace_back(r[i].second + 1);
        }

        cout << a.size() << ' ';
        for(auto i : a) cout << i << ' ';
        cout << '\n';
        cout << b.size() << ' ';
        for(auto i : b) cout << i << ' ';
        cout << '\n';
    }
}